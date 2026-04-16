from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Coroutine, Any

import config
from solana.rpc.async_api import AsyncClient
from telethon import TelegramClient, events
from core.scanner import _process_new_token
from services.cache import is_cached

logger = logging.getLogger(__name__)
CHANNELS = [item.strip()
            for item in config.TG_CHANNELS.split(",") if item.strip()]
client = TelegramClient(
    "telegram_session", config.TG_API_ID, config.TG_API_HASH)
SEEN_MINTS: set[str] = set()


def get_token_data(text: str) -> dict | None:
    """Extract token mint and metadata from a Telegram message."""
    market_cap_match = re.search(
        r"Market\s*cap:\s*\$?([0-9,]+)", text, re.IGNORECASE)
    liquidity_match = re.search(
        r"Liquidity:\s*\$?([0-9,]+)", text, re.IGNORECASE)

    market_cap = int(market_cap_match.group(1).replace(
        ",", "")) if market_cap_match else None
    liquidity = int(liquidity_match.group(1).replace(
        ",", "")) if liquidity_match else None

    mint = None
    ca_match = re.search(r"\b([1-9A-HJ-NP-Za-km-z]{39,40}pump)\b", text)
    if ca_match:
        mint = ca_match.group(1).strip()

    if not mint:
        for line in text.split("\n"):
            line = line.strip()
            if re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", line):
                if len(line) >= 32 and not any(char in line for char in ["I", "O", "l", "0"]):
                    mint = line
                    break

    if not mint:
        ca_prefix_match = re.search(
            r"(?:CA|Contract|Address|Token):\s*([1-9A-HJ-NP-Za-km-z]{32,44})",
            text,
            re.IGNORECASE,
        )
        if ca_prefix_match:
            mint = ca_prefix_match.group(1).strip()

    if not mint:
        solana_address_match = re.search(
            r"\b([1-9A-HJ-NP-Za-km-z]{43,44})\b", text)
        if solana_address_match:
            candidate = solana_address_match.group(1)
            if not any(char in candidate for char in ["I", "O", "l", "0"]):
                mint = candidate

    if not mint:
        return None

    return {
        "mint": mint,
        "entry_market_cap": market_cap or 0,
        "liquidity": liquidity or 0,
    }


def _register_telegram_handlers(
    on_whale_found: Callable[[str, list[str]], Coroutine[Any, Any, None]],
    rpc_client: AsyncClient,
    helius_api_key: str,
) -> None:
    @client.on(events.NewMessage(chats=CHANNELS))
    async def handle_new_message(event):
        try:
            text = event.message.message or ""
            structured = get_token_data(text)
            if not structured:
                return

            mint = structured["mint"]
            if mint in SEEN_MINTS or await is_cached(mint):
                logger.debug(
                    "[Telegram] Token %s already processed, skipping", mint)
                return

            SEEN_MINTS.add(mint)
            logger.info("[Telegram] 🆕 New token detected: %s", mint)
            logger.info(
                "[Telegram] MC: $%s, Liq: $%s",
                f"{structured['entry_market_cap']:,}",
                f"{structured['liquidity']:,}",
            )

            asyncio.create_task(
                _process_new_token(mint, helius_api_key,
                                   rpc_client, on_whale_found)
            )
        except Exception as exc:
            logger.exception("[Telegram] Error processing message: %s", exc)


async def start_telegram_listener(
    on_whale_found: Callable[[str, list[str]], Coroutine[Any, Any, None]],
    rpc_client: AsyncClient,
    helius_api_key: str,
) -> None:
    if not helius_api_key:
        logger.warning("HELIUS_API_KEY not set — backscan will fail.")

    _register_telegram_handlers(on_whale_found, rpc_client, helius_api_key)

    retry_delay = 2
    max_retry_delay = 60
    attempt = 0

    while True:
        attempt += 1
        try:
            logger.info(
                "[Telegram] Listening for messages on %s... Attempt %d", CHANNELS, attempt)
            await client.start()
            await client.run_until_disconnected()
            logger.info("[Telegram] Connection lost. Reconnecting...")
            retry_delay = 2
        except (OSError, ConnectionError, Exception) as exc:
            logger.warning(
                "[Telegram] Network error: %s — reconnecting in %ds.", exc, retry_delay)
            try:
                await client.disconnect()
            except Exception:
                pass

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, max_retry_delay)
