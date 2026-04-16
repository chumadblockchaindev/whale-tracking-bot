import json
import os
import pandas as pd
from services.global_vars import (
    COMMON_COLUMN_DTYPES,
    DEFAULT_ROW,
    COLUMNS,
    TRADE_COLUMNS,
    TRADE_DEFAULT_ROW,
)
import aiofiles
import time
import io


def load_config(path="config.json"):
    with open(path, "r") as f:
        return json.load(f)

# def get_age_seconds(ts: int) -> int:
#     """Calculate how many seconds ago a given Unix timestamp occurred."""
#     return int(time.time()) - ts


def ensure_csv_exists(csv_path, default_row=None, columns=None):
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    if not os.path.exists(csv_path) or os.stat(csv_path).st_size == 0:
        if default_row is not None:
            df = pd.DataFrame([default_row])
        elif columns is not None:
            df = pd.DataFrame(columns=columns)
        else:
            df = pd.DataFrame()
        df.to_csv(csv_path, index=False, encoding="utf-8")


def remove_old_tokens(csv_path, max_age_min=10):
    now = pd.Timestamp.utcnow()

    # Load CSV with dtypes enforced
    df = pd.read_csv(csv_path, dtype=COMMON_COLUMN_DTYPES, encoding="utf-8")

    if df.empty or 'created_at' not in df:
        return

    # Ensure created_at is numeric
    df['created_at'] = pd.to_numeric(df['created_at'], errors='coerce')

    # Drop invalid timestamps
    df = df[df['created_at'].notna()]

    # Compute age in minutes
    df['age_min'] = (now - pd.to_datetime(df['created_at'],
                     unit='s', utc=True)).dt.total_seconds() / 60

    # Keep only tokens younger than max_age_min
    old_tokens = df[df['age_min'] > max_age_min]
    df = df[df['age_min'] <= max_age_min]

    print(
        f"[Cleanup] Dropped {len(old_tokens)} old tokens older than {max_age_min} minutes.")

    # Drop helper column before saving
    df.drop(columns=['age_min'], inplace=True)

    df.to_csv(csv_path, index=False, encoding="utf-8")


async def append_token_to_csv(token_data: dict, csv_file: str):
    mint = token_data.get("mint")
    if not mint:
        return  # Ignore if no mint key

    # ✅ Check if file exists and mint is already inside
    if os.path.exists(csv_file) and os.stat(csv_file).st_size > 0:
        try:
            df_existing = pd.read_csv(csv_file, usecols=["mint"])
            if mint in df_existing["mint"].values:
                print(f"[CSV] Skipping duplicate token: {mint}")
                return
        except Exception as e:
            print(f"[CSV] Error checking duplicates: {e}")

    # Fill missing fields with defaults (keep all columns in sync)
    full_row = DEFAULT_ROW.copy()
    full_row.update(token_data)
    full_row["created_at"] = int(time.time())

    # ✅ Guarantee column order and fill any missing keys
    row = {col: full_row.get(col, DEFAULT_ROW[col]) for col in COLUMNS}
    df = pd.DataFrame([row], columns=COLUMNS)

    # Write header only if file is new/empty
    write_header = not os.path.exists(
        csv_file) or os.stat(csv_file).st_size == 0

    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=write_header)

    async with aiofiles.open(csv_file, mode="a", encoding="utf-8") as f:
        await f.write(buffer.getvalue())

    print(f"[CSV] Appended new token: {mint}")


async def append_whale_to_csv(
    token_mint: str,
    whale_wallet: str,
    whale_wallet_count: int,
    whale_roi_pct: float,
    whale_age_hours: float,
    csv_file: str,
    market_cap: float = 0.0,
    dev_wallet: str = "",
) -> None:
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)

    if os.path.exists(csv_file) and os.stat(csv_file).st_size > 0:
        try:
            df = pd.read_csv(
                csv_file, dtype=COMMON_COLUMN_DTYPES, encoding="utf-8")
        except Exception:
            df = pd.DataFrame(columns=COLUMNS)
    else:
        df = pd.DataFrame(columns=COLUMNS)

    token_mask = df["mint"].astype(str) == token_mint
    if token_mask.any():
        idx = token_mask.idxmax()
        df.at[idx, "whale_wallet"] = whale_wallet
        df.at[idx, "whale_wallet_count"] = max(
            int(df.at[idx, "whale_wallet_count"] or 0), whale_wallet_count
        )
        df.at[idx, "whale_roi_pct"] = whale_roi_pct
        df.at[idx, "whale_age_hours"] = whale_age_hours
        df.at[idx, "market_cap"] = market_cap
        df.at[idx, "dev_wallet"] = dev_wallet
    else:
        row = DEFAULT_ROW.copy()
        row.update({
            "mint": token_mint,
            "whale_wallet": whale_wallet,
            "whale_wallet_count": whale_wallet_count,
            "whale_roi_pct": whale_roi_pct,
            "whale_age_hours": whale_age_hours,
            "market_cap": market_cap,
            "dev_wallet": dev_wallet,
            "created_at": int(time.time()),
        })
        row = {col: row.get(col, DEFAULT_ROW[col]) for col in COLUMNS}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    async with aiofiles.open(csv_file, mode="w", encoding="utf-8") as f:
        await f.write(buffer.getvalue())

    print(f"[CSV] Updated whale record for token: {token_mint}")


async def append_trade_to_csv(trade_data: dict, csv_file: str):
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    full_row = TRADE_DEFAULT_ROW.copy()
    full_row.update(trade_data)
    full_row["timestamp"] = int(time.time())

    row = {col: full_row.get(
        col, TRADE_DEFAULT_ROW[col]) for col in TRADE_COLUMNS}
    df = pd.DataFrame([row], columns=TRADE_COLUMNS)
    write_header = not os.path.exists(
        csv_file) or os.stat(csv_file).st_size == 0

    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=write_header)

    async with aiofiles.open(csv_file, mode="a", encoding="utf-8") as f:
        await f.write(buffer.getvalue())

    print(
        f"[CSV] Appended trade record for: {trade_data.get('token_mint', 'unknown')}")


def safe_get(data: dict, key: str, default=0):
    """Safely get value from dict, ensuring it's never None"""
    value = data.get(key, default)
    return default if value is None else value


def safe_str(value, default=''):
    """Safely convert to string, handling None"""
    if value is None:
        return default
    return str(value)


def safe_float(value, default=0.0):
    """Safely convert to float, handling None"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default=0):
    """Safely convert to int, handling None"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def format_birdeye_to_jupiter(birdeye_data: dict) -> dict:
    """
    Convert Birdeye token data format to Jupiter-compatible format
    Handles None values and missing fields properly
    """

    if not birdeye_data or not isinstance(birdeye_data, dict):
        return {}

    # Extract extensions safely
    extensions = birdeye_data.get('extensions')
    if extensions is None:
        extensions = {}

    # Build stats objects
    def build_stats(timeframe: str) -> dict:
        """Build stats object for a specific timeframe"""
        return {
            "priceChange": safe_float(birdeye_data.get(f'priceChange{timeframe}Percent')),
            "holderChange": safe_float(birdeye_data.get(f'uniqueWallet{timeframe}ChangePercent')),
            "liquidityChange": 0.0,
            "volumeChange": safe_float(birdeye_data.get(f'v{timeframe}ChangePercent')),
            "buyVolume": safe_float(birdeye_data.get(f'vBuy{timeframe}USD')),
            "sellVolume": safe_float(birdeye_data.get(f'vSell{timeframe}USD')),
            "buyOrganicVolume": 0.0,
            "sellOrganicVolume": 0.0,
            "numBuys": safe_int(birdeye_data.get(f'buy{timeframe}')),
            "numSells": safe_int(birdeye_data.get(f'sell{timeframe}')),
            "numTraders": safe_int(birdeye_data.get(f'uniqueWallet{timeframe}')),
            "numOrganicBuyers": 0,
            "numNetBuyers": (
                safe_int(birdeye_data.get(f'buy{timeframe}')) -
                safe_int(birdeye_data.get(f'sell{timeframe}'))
            )
        }

    # Convert to Jupiter format
    jupiter_format = {
        "id": safe_str(birdeye_data.get('address'), ''),
        "name": safe_str(birdeye_data.get('name'), ''),
        "symbol": safe_str(birdeye_data.get('symbol'), ''),
        "icon": safe_str(birdeye_data.get('logoURI'), ''),
        "decimals": safe_int(birdeye_data.get('decimals'), 6),
        "twitter": safe_str(extensions.get('twitter'), ''),
        "tiktok": safe_str(extensions.get('tiktok'), ''),
        "telegram": safe_str(extensions.get('telegram'), ''),
        "website": safe_str(extensions.get('website'), ''),
        "dev": safe_str(birdeye_data.get('dev'), ''),
        "circSupply": safe_float(birdeye_data.get('circulatingSupply')),
        "totalSupply": safe_float(birdeye_data.get('totalSupply')),
        "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "launchpad": "pump.fun",
        "firstPool": {
            "id": safe_str(birdeye_data.get('address'), ''),
            "createdAt": safe_str(birdeye_data.get('createdAt'), '')
        },
        "graduatedPool": safe_str(birdeye_data.get('graduatedPool'), ''),
        "graduatedAt": safe_str(birdeye_data.get('graduatedAt'), ''),
        "holderCount": safe_int(birdeye_data.get('holder')),
        "audit": {
            "mintAuthorityDisabled": False,
            "freezeAuthorityDisabled": False,
            "topHoldersPercentage": safe_float(birdeye_data.get('topHoldersPercentage')),
            "devMigrations": 0,
            "devMints": 0,
            "devBalancePercentage": safe_float(birdeye_data.get('devBalancePercentage'))
        },
        "dexBanner": safe_str(birdeye_data.get('dexBanner'), ''),
        "organicScore": safe_float(birdeye_data.get('organicScore')),
        "organicScoreLabel": safe_str(birdeye_data.get('organicScoreLabel'), 'unknown'),
        "tags": ["birdeye"],
        "createdAt": safe_str(birdeye_data.get('createdAt'), ''),
        "fdv": safe_float(birdeye_data.get('fdv')),
        "mcap": safe_float(birdeye_data.get('marketCap')),
        "usdPrice": safe_float(birdeye_data.get('price')),
        "priceBlockId": 0,
        "liquidity": safe_float(birdeye_data.get('liquidity')),

        # Stats for different timeframes
        "stats5m": build_stats('5m'),
        "stats1h": build_stats('1h'),
        "stats6h": build_stats('6h'),
        "stats24h": build_stats('24h'),

        "fees": 0.0,
        "bondingCurve": safe_float(birdeye_data.get('bondingCurve')),
        "updatedAt": safe_str(birdeye_data.get('lastTradeHumanTime'), '')
    }

    return jupiter_format
