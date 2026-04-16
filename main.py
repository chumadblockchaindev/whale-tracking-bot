from __future__ import annotations

import asyncio
import logging
import sys

import config
from core.executor import ExecutionEngine
from core.scanner import monitor_whale_wallets
from core.telegram_listener import start_telegram_listener
from services.cache import init_cache
from services.global_vars import (
    COLUMNS,
    CSV_FILE,
    TRADE_COLUMNS,
    TRADE_CSV_FILE,
)
from services.http_session import close_session
from services.utils import ensure_csv_exists
from solana.rpc.async_api import AsyncClient

# ── Logging Setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ── Main Orchestrator ─────────────────────────────────────────────────────────

async def main() -> None:
    config.validate()
    logger.info("═" * 60)
    logger.info("  Solana Whale-Tracking & Copy-Trading Bot  ")
    logger.info("═" * 60)

    ensure_csv_exists(CSV_FILE, columns=COLUMNS)
    ensure_csv_exists(TRADE_CSV_FILE, columns=TRADE_COLUMNS)
    await init_cache()

    rpc_client = AsyncClient(config.RPC_HTTP_URL)
    engine = ExecutionEngine(rpc_client)

    async def on_whale_found(token_mint: str, whale_addresses: list[str]) -> None:
        if not whale_addresses:
            return

        logger.info(
            "🐳 %d whale(s) found for token %s — starting live monitor.",
            len(whale_addresses), token_mint[:8],
        )
        await monitor_whale_wallets(
            whale_addresses=whale_addresses,
            token_mint=token_mint,
            on_buy_signal=engine.handle_buy_signal,
        )

    try:
        tasks = [
            asyncio.create_task(
                start_telegram_listener(
                    on_whale_found=on_whale_found,
                    rpc_client=rpc_client,
                    helius_api_key=config.HELIUS_API_KEY,
                )
            ),
        ]
        await asyncio.gather(*tasks)
    finally:
        await rpc_client.close()
        await close_session()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
        sys.exit(0)
