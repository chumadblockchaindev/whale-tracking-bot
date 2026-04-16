"""
executor.py — Real-Time Trade Execution Engine.

Responsibilities:
  1. Build and sign Raydium swap transactions.
  2. Wrap transactions in a Jito bundle with a tip to co-land with the whale.
  3. Apply dynamic priority fees.
  4. Track open positions and enforce Trailing Stop-Loss + Take-Profit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from solders.compute_budget import set_compute_unit_price

import config
from core.filters import is_token_safe
from services.global_vars import TRADE_CSV_FILE
from services.telegram import send_trade_message
from services.utils import append_trade_to_csv

logger = logging.getLogger(__name__)

# Jito tip accounts (rotate to spread load)
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt13slots",
]


# ─────────────────────────────────────────────────────────────────────────────
# Position Tracker
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Position:
    token_mint:      str
    entry_price_sol: float
    token_amount:    float          # tokens held
    peak_price_sol:  float = field(init=False)
    opened_at:       float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.peak_price_sol = self.entry_price_sol

    def update_peak(self, current_price: float) -> None:
        if current_price > self.peak_price_sol:
            self.peak_price_sol = current_price

    def trailing_stop_triggered(self, current_price: float) -> bool:
        drop_from_peak = (self.peak_price_sol - current_price) / self.peak_price_sol * 100
        return drop_from_peak >= config.TRAILING_STOP_PCT

    def take_profit_triggered(self, current_price: float) -> bool:
        gain = (current_price - self.entry_price_sol) / self.entry_price_sol * 100
        return gain >= config.TAKE_PROFIT_PCT


# ─────────────────────────────────────────────────────────────────────────────
# Execution Engine
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionEngine:

    def __init__(self, rpc_client: AsyncClient) -> None:
        self.client  = rpc_client
        self.keypair = Keypair.from_base58_string(config.PRIVATE_KEY_B58)
        self.wallet  = self.keypair.pubkey()
        self.positions: dict[str, Position] = {}   # mint → Position
        logger.info("ExecutionEngine initialised. Wallet: %s", self.wallet)

    # ── Public entry point called by scanner ─────────────────────────────────

    async def handle_buy_signal(
        self,
        whale: str,
        token_mint: str,
        whale_sol_amount: float,
    ) -> None:
        """
        Called when a whale buy is detected. Runs safety checks then executes.
        """
        logger.info(
            "📥 Buy signal received | whale %s | token %s | whale spent %.3f SOL",
            whale[:8], token_mint[:8], whale_sol_amount,
        )

        # 1. Rug-pull safety check
        if not await is_token_safe(token_mint, self.client):
            logger.warning("🚫 Rug-pull check FAILED for %s — aborting buy.", token_mint)
            return

        # 2. Don't stack positions on the same token
        if token_mint in self.positions:
            logger.info("ℹ️  Already in a position for %s — skipping.", token_mint)
            return

        # 3. Execute copy buy
        await self._execute_buy(token_mint, config.DEFAULT_BUY_SOL)

    # ── Buy Execution ─────────────────────────────────────────────────────────

    async def _execute_buy(self, token_mint: str, sol_amount: float) -> None:
        logger.info("⚡ Executing BUY %.3f SOL → %s", sol_amount, token_mint[:8])

        try:
            priority_fee = await self._calculate_priority_fee()
            tx_bytes     = await self._build_swap_transaction(
                token_mint, sol_amount, priority_fee, side="buy"
            )
            sig = await self._send_via_jito(tx_bytes, token_mint)

            if sig:
                self.positions[token_mint] = Position(
                    token_mint=token_mint,
                    entry_price_sol=sol_amount,
                    token_amount=1.0,   # placeholder — update with actual fill
                )
                logger.info("✅ BUY confirmed | sig %s", sig)
                await self._record_trade({
                    "token_mint": token_mint,
                    "side": "buy",
                    "quantity": 1.0,
                    "entry_price_sol": sol_amount,
                    "exit_price_sol": 0.0,
                    "profit_sol": 0.0,
                    "roi_pct": 0.0,
                    "reason": "ENTRY",
                })
                asyncio.create_task(self._monitor_position(token_mint))

        except Exception as exc:
            logger.error("BUY execution failed for %s: %s", token_mint, exc)

    # ── Sell Execution ────────────────────────────────────────────────────────

    async def _execute_sell(self, token_mint: str, reason: str) -> None:
        logger.info("💰 Executing SELL — reason: %s | token %s", reason, token_mint[:8])

        position = self.positions.get(token_mint)
        exit_price = 0.0
        profit_sol = 0.0
        roi_pct = 0.0

        if position is not None:
            try:
                exit_price = await self._fetch_token_price_sol(token_mint)
                profit_sol = (exit_price - position.entry_price_sol) * position.token_amount
                roi_pct = (
                    profit_sol / position.entry_price_sol * 100
                    if position.entry_price_sol else 0.0
                )
            except Exception as exc:
                logger.debug("Could not fetch exit price for %s: %s", token_mint, exc)

        try:
            priority_fee = await self._calculate_priority_fee()
            tx_bytes     = await self._build_swap_transaction(
                token_mint, 0.0, priority_fee, side="sell"
            )
            sig = await self._send_via_jito(tx_bytes, token_mint)
            if sig:
                self.positions.pop(token_mint, None)
                logger.info("✅ SELL confirmed (%s) | sig %s", reason, sig)
                await self._record_trade({
                    "token_mint": token_mint,
                    "side": "sell",
                    "quantity": position.token_amount if position else 0.0,
                    "entry_price_sol": position.entry_price_sol if position else 0.0,
                    "exit_price_sol": exit_price,
                    "profit_sol": profit_sol,
                    "roi_pct": roi_pct,
                    "reason": reason,
                })
                await send_trade_message({
                    "mint": token_mint,
                    "entry_price": position.entry_price_sol if position else 0.0,
                    "exit_price": exit_price,
                    "roi": roi_pct,
                    "reason": reason,
                    "hold_seconds": int(time.time() - position.opened_at) if position else 0,
                    "exit_marketcap": 0.0,
                })

        except Exception as exc:
            logger.error("SELL execution failed for %s: %s", token_mint, exc)

    async def _record_trade(self, trade_data: dict) -> None:
        try:
            await append_trade_to_csv(trade_data, TRADE_CSV_FILE)
        except Exception as exc:
            logger.warning("Failed to persist trade record for %s: %s", trade_data.get('token_mint'), exc)

    # ── Position Monitor (Trailing Stop / Take Profit) ────────────────────────

    async def _monitor_position(self, token_mint: str, poll_seconds: float = 5.0) -> None:
        """Polls price and enforces stop-loss / take-profit."""
        logger.info("📊 Monitoring position for %s", token_mint[:8])

        while token_mint in self.positions:
            await asyncio.sleep(poll_seconds)
            position = self.positions.get(token_mint)
            if position is None:
                break

            try:
                current_price = await self._fetch_token_price_sol(token_mint)
            except Exception as exc:
                logger.debug("Price fetch error for %s: %s", token_mint, exc)
                continue

            position.update_peak(current_price)

            if position.take_profit_triggered(current_price):
                await self._execute_sell(token_mint, reason="TAKE_PROFIT")
                break

            if position.trailing_stop_triggered(current_price):
                await self._execute_sell(token_mint, reason="TRAILING_STOP")
                break

    # ── Transaction Builder ───────────────────────────────────────────────────

    async def _build_swap_transaction(
        self,
        token_mint: str,
        sol_amount: float,
        priority_fee_microlamports: int,
        side: str,
    ) -> bytes:
        """
        Constructs a versioned Raydium/Pump.fun swap transaction.

        NOTE: A production implementation integrates Raydium's SDK or uses
        Jupiter's /swap API to get the fully encoded instruction set.
        This scaffold demonstrates the structure and signing pipeline.
        For mainnet use, replace _build_swap_instruction() with a real
        Jupiter quote + swap instruction.
        """
        recent_blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = Hash.from_string(str(recent_blockhash_resp.value.blockhash))

        compute_budget_ix = set_compute_unit_price(priority_fee_microlamports)

        swap_ix = await self._build_swap_instruction(token_mint, sol_amount, side)

        message = MessageV0.try_compile(
            payer=self.wallet,
            instructions=[compute_budget_ix, swap_ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(message, [self.keypair])
        return bytes(tx)

    async def _build_swap_instruction(
        self, token_mint: str, sol_amount: float, side: str
    ) -> Instruction:
        """
        Placeholder that builds a minimal instruction pointing at Raydium AMM.

        ─── REPLACE IN PRODUCTION ───────────────────────────────────────────
        Use Jupiter Aggregator v6 /swap endpoint:
          POST https://quote-api.jup.ag/v6/swap
          Body: { inputMint, outputMint, amount, slippageBps, userPublicKey }
        Decode the returned swapTransaction (base64 VersionedTransaction),
        extract its instructions, and insert them here.
        ─────────────────────────────────────────────────────────────────────
        """
        program_id  = Pubkey.from_string(config.RAYDIUM_AMM_PROGRAM)
        token_pubkey = Pubkey.from_string(token_mint)

        return Instruction(
            program_id=program_id,
            accounts=[
                AccountMeta(pubkey=self.wallet,   is_signer=True,  is_writable=True),
                AccountMeta(pubkey=token_pubkey,  is_signer=False, is_writable=True),
            ],
            data=b"",  # Must be replaced with real swap discriminator + args
        )

    # ── Jito Bundle Sender ────────────────────────────────────────────────────

    async def _send_via_jito(self, tx_bytes: bytes, token_mint: str) -> Optional[str]:
        """
        Wraps the swap transaction in a Jito bundle with a tip transaction.
        The tip ensures the bundle is prioritised and lands in the same block.
        """
        tip_tx_bytes = await self._build_tip_transaction()
        bundle_txs   = [
            base64.b64encode(tx_bytes).decode(),
            base64.b64encode(tip_tx_bytes).decode(),
        ]

        payload = {
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "sendBundle",
            "params":  [bundle_txs],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{config.JITO_BLOCK_ENGINE_URL}/api/v1/bundles",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()

        if "error" in result:
            logger.error("Jito bundle error: %s", result["error"])
            return None

        bundle_id = result.get("result", "unknown")
        logger.info("📦 Jito bundle submitted: %s", bundle_id)
        return bundle_id

    async def _build_tip_transaction(self) -> bytes:
        """Sends JITO_TIP_LAMPORTS to a Jito tip account."""
        import random
        tip_account = Pubkey.from_string(random.choice(JITO_TIP_ACCOUNTS))

        recent_blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = Hash.from_string(str(recent_blockhash_resp.value.blockhash))

        tip_ix = transfer(TransferParams(
            from_pubkey=self.wallet,
            to_pubkey=tip_account,
            lamports=config.JITO_TIP_LAMPORTS,
        ))

        message = MessageV0.try_compile(
            payer=self.wallet,
            instructions=[tip_ix],
            address_lookup_table_accounts=[],
            recent_blockhash=blockhash,
        )
        tx = VersionedTransaction(message, [self.keypair])
        return bytes(tx)

    # ── Dynamic Priority Fee ──────────────────────────────────────────────────

    async def _calculate_priority_fee(self) -> int:
        """
        Fetches recent prioritisation fees from the network and sets the bot's
        fee to the 75th percentile, capped at MAX_PRIORITY_FEE_MICROLAMPORTS.
        """
        try:
            resp = await self.client.get_recent_prioritization_fees()
            fees = [f.prioritization_fee for f in resp.value if f.prioritization_fee > 0]
            if not fees:
                return config.BASE_PRIORITY_FEE_MICROLAMPORTS

            fees.sort()
            p75_index = int(len(fees) * 0.75)
            p75_fee   = fees[min(p75_index, len(fees) - 1)]

            clamped = max(
                config.BASE_PRIORITY_FEE_MICROLAMPORTS,
                min(p75_fee, config.MAX_PRIORITY_FEE_MICROLAMPORTS),
            )
            logger.debug("Dynamic priority fee: %d µlamports", clamped)
            return clamped

        except Exception as exc:
            logger.warning("Fee calculation fallback: %s", exc)
            return config.BASE_PRIORITY_FEE_MICROLAMPORTS

    # ── Price Oracle (simplified) ─────────────────────────────────────────────

    async def _fetch_token_price_sol(self, token_mint: str) -> float:
        """
        Fetches current token price in SOL from Jupiter Price API.
        Replace with a more robust oracle (Pyth, Switchboard) for production.
        """
        url = f"https://price.jup.ag/v6/price?ids={token_mint}&vsToken=So11111111111111111111111111111111111111112"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
        price = data["data"][token_mint]["price"]
        return float(price)