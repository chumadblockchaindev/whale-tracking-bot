"""
config.py — Central configuration loader.
All secrets come from environment variables via .env — never hardcoded.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # Reads .env file from project root


# ── RPC / WebSocket Endpoints ────────────────────────────────────────────────
RPC_HTTP_URL: str = os.getenv("RPC_HTTP_URL", "")          # e.g. Helius HTTPS
RPC_WS_URL: str = os.getenv("RPC_WS_URL",   "")          # e.g. Helius WSS
JITO_BLOCK_ENGINE_URL: str = os.getenv(
    "JITO_BLOCK_ENGINE_URL",
    "https://mainnet.block-engine.jito.wtf"
)

# ── Wallet ───────────────────────────────────────────────────────────────────
# Private key stored as a base-58 string in .env — loaded once, never logged.
PRIVATE_KEY_B58: str = os.getenv("WALLET_PRIVKEY", "")

# ── Pump.fun / Raydium Program IDs ───────────────────────────────────────────
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

# ── Whale Discovery Thresholds ───────────────────────────────────────────────
WHALE_MIN_ROI_PCT:    float = 50.0   # minimum ROI % to qualify as whale
WHALE_MIN_SOL_PROFIT: float = 5.0    # minimum SOL profit to qualify
WHALE_MIN_BUY_SOL:    float = 2.0    # anti-bait: ignore buys below this
BACKSCAN_TX_COUNT:    int = 50     # how many early txns to analyse

# ── Risk Parameters ──────────────────────────────────────────────────────────
TRAILING_STOP_PCT:    float = 15.0   # trailing stop-loss %
TAKE_PROFIT_PCT:      float = 50.0   # take-profit %
MAX_SLIPPAGE_BPS:     int = 1000   # 10 % expressed in basis points
DEFAULT_BUY_SOL:      float = 0.25   # SOL to spend per copy-trade

# ── Priority Fees ────────────────────────────────────────────────────────────
BASE_PRIORITY_FEE_MICROLAMPORTS: int = 100_000   # floor
MAX_PRIORITY_FEE_MICROLAMPORTS:  int = 5_000_000  # ceiling
JITO_TIP_LAMPORTS:               int = 10_000    # Jito tip per bundle

# ── Misc ─────────────────────────────────────────────────────────────────────
FRESH_WALLET_AGE_HOURS: int = 24    # wallets newer than this are filtered out
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── External Service Keys / Telegram ───────────────────────────────────────────
HELIUS_API_KEY: str = os.getenv("HELIUS_API_KEY", "")
TG_API_ID: int = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH: str = os.getenv("TG_API_HASH", "")
TG_CHANNELS: str = os.getenv("TG_CHANNELS", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def validate() -> None:
    """Call once at startup to catch missing critical env vars."""
    missing = [k for k, v in {
        "RPC_HTTP_URL":          RPC_HTTP_URL,
        "RPC_WS_URL":            RPC_WS_URL,
        "WALLET_PRIVATE_KEY_B58": PRIVATE_KEY_B58,
        "HELIUS_API_KEY":        HELIUS_API_KEY,
        "TG_API_ID":             TG_API_ID,
        "TG_API_HASH":           TG_API_HASH,
        "TG_CHANNELS":           TG_CHANNELS,
    }.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Please check your .env file."
        )
