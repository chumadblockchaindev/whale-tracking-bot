"""
scanner.py — Graduation Scanner & Whale Monitor.

Responsibilities:
  1. Poll the Pump.fun graduation API for newly graduated tokens.
  2. Trigger the back-scan → whale discovery pipeline per new token.
  3. Open a Helius WebSocket logsSubscribe session for each whale wallet.
  4. Parse incoming logs for Raydium / Pump.fun swap instructions.
  5. Dispatch buy signals to the executor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Coroutine, Any

import aiohttp
from solana.rpc.async_api import AsyncClient

import config
from core.filters import backscan_and_find_whales, is_buy_large_enough
from services.cache import cache_token, is_cached

logger = logging.getLogger(__name__)

PUMP_GRADUATION_API = (
    "https://frontend-api.pump.fun/coins?sort=last_trade_timestamp&order=DESC"
    "&offset=0&limit=50&includeNsfw=true"
)

RAYDIUM_SWAP_LOG_FRAGMENT = "Program log: ray_log:"
PUMP_SWAP_LOG_FRAGMENT = "Program log: Instruction: Buy"

# _seen_tokens: set[str] = set()
_subscribed_wallets: set[str] = set()


# async def start_pumpfun_poller(
#     on_whale_found: Callable[[str, list[str]], Coroutine[Any, Any, None]],
#     rpc_client: AsyncClient,
#     helius_api_key: str,
#     poll_interval: float = 30.0,
# ) -> None:
#     if not helius_api_key:
#         logger.warning("HELIUS_API_KEY not set — backscan will fail.")

#     while True:
#         try:
#             mints = await _fetch_pumpfun_graduated_tokens()
#             if not mints:
#                 logger.debug("Pump.fun poller returned no new tokens.")

#             for mint in mints:
#                 if mint in _seen_tokens or await is_cached(mint):
#                     continue

#                 _seen_tokens.add(mint)
#                 asyncio.create_task(
#                     _process_new_token(mint, helius_api_key,
#                                        rpc_client, on_whale_found)
#                 )
#         except Exception as exc:
#             logger.warning("Pump.fun poller error: %s", exc)

#         await asyncio.sleep(poll_interval)


# async def _fetch_pumpfun_graduated_tokens() -> list[str]:
#     async with aiohttp.ClientSession() as session:
#         async with session.get(PUMP_GRADUATION_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
#             if resp.status != 200:
#                 logger.warning(
#                     "Pump.fun endpoint returned status %s", resp.status)
#                 return []
#             payload = await resp.json()

#     items = []
#     if isinstance(payload, dict):
#         if isinstance(payload.get("data"), list):
#             items = payload["data"]
#         elif isinstance(payload.get("coins"), list):
#             items = payload["coins"]
#         else:
#             items = [payload]
#     elif isinstance(payload, list):
#         items = payload

#     mints: list[str] = []
#     for item in items:
#         mint = _extract_mint_from_response(item)
#         if mint:
#             mints.append(mint)
#     return mints


def _extract_mint_from_response(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None

    for key in ("mint", "tokenMint", "address", "id", "tokenAddress"):
        value = item.get(key)
        if isinstance(value, str) and len(value) >= 32:
            return value
    return None


async def _process_new_token(
    mint: str,
    helius_api_key: str,
    rpc_client: AsyncClient,
    on_whale_found: Callable[[str, list[str]], Coroutine[Any, Any, None]],
) -> None:
    try:
        whales = await backscan_and_find_whales(mint, helius_api_key, rpc_client)
        await cache_token(mint)
        if whales:
            await on_whale_found(mint, whale_addresses=whales)
    except Exception as exc:
        logger.error("Error processing token %s: %s", mint, exc)


async def monitor_whale_wallets(
    whale_addresses: list[str],
    token_mint: str,
    on_buy_signal: Callable[[str, str, float], Coroutine[Any, Any, None]],
) -> None:
    new_whales = [w for w in whale_addresses if w not in _subscribed_wallets]
    if not new_whales:
        return

    tasks = [
        asyncio.create_task(
            _subscribe_wallet_logs(whale, token_mint, on_buy_signal)
        )
        for whale in new_whales
    ]
    _subscribed_wallets.update(new_whales)
    logger.info("👁  Now monitoring %d new whale wallet(s).", len(new_whales))
    _ = tasks


async def _subscribe_wallet_logs(
    whale: str,
    token_mint: str,
    on_buy_signal: Callable[[str, str, float], Coroutine[Any, Any, None]],
) -> None:
    subscribe_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [whale]},
            {"commitment": "processed"},
        ],
    })

    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    config.RPC_WS_URL,
                    heartbeat=30,
                ) as ws:
                    await ws.send_str(subscribe_msg)
                    logger.debug("📡 Subscribed to logs for whale %s", whale)

                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        await _handle_log_message(
                            msg.data, whale, token_mint, on_buy_signal
                        )

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(
                "WebSocket error for %s: %s — reconnecting in 5s.", whale, exc)
            await asyncio.sleep(5)


async def _handle_log_message(
    raw: str,
    whale: str,
    token_mint: str,
    on_buy_signal: Callable[[str, str, float], Coroutine[Any, Any, None]],
) -> None:
    try:
        data = json.loads(raw)
        logs: list[str] = (
            data.get("params", {})
                .get("result", {})
                .get("value", {})
                .get("logs", [])
        )
    except (json.JSONDecodeError, AttributeError):
        return

    is_buy = any(
        RAYDIUM_SWAP_LOG_FRAGMENT in log or PUMP_SWAP_LOG_FRAGMENT in log
        for log in logs
    )
    if not is_buy:
        return

    sol_amount = _extract_sol_amount_from_logs(logs)

    logger.info(
        "🐳 Whale %s BUY detected | token %s | ~%.3f SOL",
        whale[:8] + "…", token_mint[:8] + "…", sol_amount,
    )

    if not is_buy_large_enough(sol_amount):
        return

    await on_buy_signal(whale, token_mint, sol_amount)


def _extract_sol_amount_from_logs(logs: list[str]) -> float:
    import base64
    import struct

    for log in logs:
        if RAYDIUM_SWAP_LOG_FRAGMENT in log:
            try:
                b64_part = log.split("ray_log:")[1].strip()
                decoded = base64.b64decode(b64_part)
                if len(decoded) >= 16:
                    amount_in_lamports, = struct.unpack_from("<Q", decoded, 8)
                    return amount_in_lamports / 1e9
            except Exception:
                pass

    return 0.0
