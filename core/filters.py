"""
filters.py — Whale discovery & safety filters.

Responsibilities:
  1. Back-scan the first N transactions of a graduated token.
  2. Score wallets by ROI and profit → return qualified "whale" addresses.
  3. Filter out developer wallets, fresh wallets, and dust buys.
  4. Rug-pull safety check: verify Mint Authority revoked + Freeze Authority null.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from services.global_vars import CSV_FILE
from services.utils import append_whale_to_csv
import config

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WalletStats:
    address: str
    total_invested_sol: float = 0.0
    total_received_sol:  float = 0.0
    is_developer:        bool = False
    wallet_age_hours:    float = float("inf")

    @property
    def profit_sol(self) -> float:
        return self.total_received_sol - self.total_invested_sol

    @property
    def roi_pct(self) -> float:
        if self.total_invested_sol == 0:
            return 0.0
        return (self.profit_sol / self.total_invested_sol) * 100.0

    def qualifies_as_whale(self) -> bool:
        return (
            self.roi_pct >= config.WHALE_MIN_ROI_PCT
            and self.profit_sol >= config.WHALE_MIN_SOL_PROFIT
            and not self.is_developer
            and self.wallet_age_hours >= config.FRESH_WALLET_AGE_HOURS
        )


# ─────────────────────────────────────────────────────────────────────────────
# Rug-Pull Safety Check
# ─────────────────────────────────────────────────────────────────────────────

async def is_token_safe(mint_address: str, client: AsyncClient) -> bool:
    """
    Returns True only if:
      • Mint Authority == null  (revoked — no one can print new tokens)
      • Freeze Authority == null (no one can freeze holder accounts)

    Uses getAccountInfo on the SPL Token mint account and parses the
    165-byte mint layout directly, avoiding heavy dependencies.
    """
    try:
        pubkey = Pubkey.from_string(mint_address)
        resp = await client.get_account_info(pubkey, encoding="base64")

        account = resp.value
        if account is None:
            logger.warning("Token %s account not found.", mint_address)
            return False

        import base64
        import struct

        data = base64.b64decode(account.data[0])

        # SPL Token Mint layout (82 bytes packed):
        #   [0:4]   mint_authority_option  (u32 COption — 0=None, 1=Some)
        #   [4:36]  mint_authority         (Pubkey, 32 bytes)
        #   [36:44] supply                 (u64)
        #   [44]    decimals               (u8)
        #   [45]    is_initialized         (bool)
        #   [46:50] freeze_authority_option (u32 COption)
        #   [50:82] freeze_authority        (Pubkey)

        if len(data) < 82:
            logger.warning("Unexpected mint data length for %s.", mint_address)
            return False

        mint_auth_option,  = struct.unpack_from("<I", data, 0)
        freeze_auth_option, = struct.unpack_from("<I", data, 46)

        mint_authority_revoked = mint_auth_option == 0
        freeze_authority_null = freeze_auth_option == 0

        if not mint_authority_revoked:
            logger.info(
                "❌ UNSAFE — %s has active Mint Authority.", mint_address)
        if not freeze_authority_null:
            logger.info(
                "❌ UNSAFE — %s has active Freeze Authority.", mint_address)

        return mint_authority_revoked and freeze_authority_null

    except Exception as exc:
        logger.error("Safety check failed for %s: %s", mint_address, exc)
        return False  # Fail closed — never buy if we can't verify


# ─────────────────────────────────────────────────────────────────────────────
# Wallet Age Check
# ─────────────────────────────────────────────────────────────────────────────

async def get_wallet_age_hours(address: str, client: AsyncClient) -> float:
    """
    Approximate wallet age by fetching the oldest transaction signature.
    Returns age in hours. Returns 0.0 on any failure (treated as fresh/unsafe).
    """
    try:
        pubkey = Pubkey.from_string(address)
        # Fetch up to 1000 signatures and get the last (oldest)
        resp = await client.get_signatures_for_address(
            pubkey, limit=1000, commitment="finalized"
        )
        sigs = resp.value
        if not sigs:
            return 0.0
        oldest_ts = sigs[-1].block_time   # Unix timestamp
        if oldest_ts is None:
            return 0.0
        age_seconds = time.time() - oldest_ts
        return age_seconds / 3600.0
    except Exception as exc:
        logger.debug("Could not fetch wallet age for %s: %s", address, exc)
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Back-Scan & Whale Discovery
# ─────────────────────────────────────────────────────────────────────────────

async def backscan_and_find_whales(
    token_mint: str,
    helius_api_key: str,
    client: AsyncClient,
) -> list[str]:
    """
    1. Fetch the first BACKSCAN_TX_COUNT transactions for `token_mint`.
    2. Parse SOL flows per wallet (buys vs. sells/withdrawals).
    3. Identify developer (first minter).
    4. Filter by ROI %, minimum profit, age.
    5. Return list of whale wallet addresses.

    Uses Helius Enhanced Transactions API for structured parsing.
    """
    url = (
        f"https://api.helius.xyz/v0/addresses/{token_mint}/transactions"
        f"?api-key={helius_api_key}&limit={config.BACKSCAN_TX_COUNT}&type=SWAP"
    )

    wallets: dict[str, WalletStats] = {}
    developer_wallet: Optional[str] = None

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.error("Helius API error %s for token %s",
                             resp.status, token_mint)
                return []
            transactions = await resp.json()

    logger.info("Back-scanning %d transactions for %s …",
                len(transactions), token_mint)

    for i, tx in enumerate(transactions):
        try:
            _parse_transaction_into_stats(tx, i, wallets, developer_wallet)
            if i == 0 and wallets:
                # Heuristic: first wallet to interact is likely the dev/deployer
                developer_wallet = next(iter(wallets))
        except Exception as exc:
            logger.debug("Skipping tx %d parse error: %s", i, exc)

    # Mark developer
    if developer_wallet and developer_wallet in wallets:
        wallets[developer_wallet].is_developer = True

    # Fetch wallet ages concurrently (rate-limit to 5 at a time)
    sem = asyncio.Semaphore(5)

    async def age_task(addr: str) -> None:
        async with sem:
            wallets[addr].wallet_age_hours = await get_wallet_age_hours(addr, client)

    await asyncio.gather(*[age_task(addr) for addr in wallets])

    # Filter to qualifying whales
    whales = [
        addr for addr, stats in wallets.items()
        if stats.qualifies_as_whale()
    ]

    logger.info(
        "Whale discovery complete for %s — %d/%d wallets qualify.",
        token_mint, len(whales), len(wallets)
    )
    if whales:
        best_whale = max(
            (wallets[addr] for addr in whales),
            key=lambda stats: stats.roi_pct,
        )
        await append_whale_to_csv(
            token_mint=token_mint,
            whale_wallet=best_whale.address,
            whale_wallet_count=len(whales),
            whale_roi_pct=best_whale.roi_pct,
            whale_age_hours=best_whale.wallet_age_hours,
            csv_file=config.CSV_FILE if hasattr(
                config, 'CSV_FILE') else 'data/tokens.csv',
        )

    for addr in whales:
        s = wallets[addr]
        logger.info(
            "  🐳 %s | ROI %.1f%% | Profit %.2f SOL | Age %.1fh",
            addr, s.roi_pct, s.profit_sol, s.wallet_age_hours
        )

    return whales


def _parse_transaction_into_stats(
    tx: dict,
    index: int,
    wallets: dict[str, WalletStats],
    developer_wallet: Optional[str],
) -> None:
    """
    Parses a Helius Enhanced Transaction object for SOL flows.
    Helius provides `nativeTransfers` and `tokenTransfers` at top level.
    """
    native_transfers = tx.get("nativeTransfers", [])

    for transfer in native_transfers:
        from_addr: str = transfer.get("fromUserAccount", "")
        to_addr:   str = transfer.get("toUserAccount",   "")
        amount_lamports: int = transfer.get("amount", 0)
        amount_sol = amount_lamports / 1e9

        if amount_sol < 0.001:  # skip dust
            continue

        # SOL flowing in = buy; SOL flowing out = sell/receive proceeds
        if from_addr:
            if from_addr not in wallets:
                wallets[from_addr] = WalletStats(address=from_addr)
            wallets[from_addr].total_invested_sol += amount_sol

        if to_addr:
            if to_addr not in wallets:
                wallets[to_addr] = WalletStats(address=to_addr)
            wallets[to_addr].total_received_sol += amount_sol


# ─────────────────────────────────────────────────────────────────────────────
# Anti-Bait Filter (used by executor)
# ─────────────────────────────────────────────────────────────────────────────

def is_buy_large_enough(sol_amount: float) -> bool:
    """Returns True only if the whale's buy exceeds the minimum threshold."""
    if sol_amount < config.WHALE_MIN_BUY_SOL:
        logger.info(
            "⚠️  Anti-bait filter triggered — buy of %.3f SOL < %.1f SOL minimum.",
            sol_amount, config.WHALE_MIN_BUY_SOL
        )
        return False
    return True
