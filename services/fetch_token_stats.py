import os
import asyncio
import random
from dotenv import load_dotenv
import time
from services.http_session import get_session
from services.utils import format_birdeye_to_jupiter


load_dotenv()

# ======================
# 🔑 API KEYS
# ======================
JUP_API_KEY = os.getenv("JUP_API_KEY")
SOLSCAN_API_KEY = os.getenv("SOLSCAN_API_KEY")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")

# ======================
# ⚙ CONFIG
# ======================
RETRY_LIMIT = 3
RETRY_DELAY = 2  # seconds
RATE_LIMIT = 5   # requests per second per API

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)",
    "Mozilla/5.0 (Android 11; Mobile; rv:89.0) Gecko/89.0 Firefox/89.0"
]

API_QUOTAS = {
    "jupiter": {"limit": 1, "window": 1.0},   # 3 requests per second
    "solscan": {"limit": 1, "window": 1.0},   # 1 request per second
    "moralis": {"limit": 3, "window": 1.0},   # 3 requests per second
    "birdeye": {"limit": 1, "window": 1.0},   # 1 requests per second
}

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/"

last_requests = {api: [] for api in API_QUOTAS}

def debug_quotas():
    print("[Quota]", {k: len(v) for k, v in last_requests.items()})

def has_quota(api: str) -> bool:
    """Check if API has available quota"""
    now = time.time()
    window = API_QUOTAS[api]["window"]
    limit = API_QUOTAS[api]["limit"]

    # Keep only requests in the window
    last_requests[api] = [t for t in last_requests[api] if now - t < window]
    return len(last_requests[api]) < limit

def record_request(api: str):
    last_requests[api].append(time.time())

async def fetch_token_stats_jupiter(mint: str) -> dict:
    if not has_quota("jupiter"):
        return {}

    record_request("jupiter")
    url = f"https://api.jup.ag/tokens/v2/search?query={mint}"
    sess = await get_session()

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            }
            if JUP_API_KEY:
                headers["x-api-key"] = f"{JUP_API_KEY}"

            async with sess.get(url, headers=headers) as res:
                if res.status == 200:
                    data = await res.json()
                    if isinstance(data, list) and data:
                        print(f"[Jupiter] ✅ {mint}")
                        return data[0]
                elif res.status == 429:
                    await asyncio.sleep(2)

        except Exception as e:
            print(f"[Jupiter] {mint}: {e}")

        await asyncio.sleep(RETRY_DELAY)

    return {}

async def fetch_token_stats_solscan(mint: str) -> dict:
    if not has_quota("solscan"):
        return {}

    record_request("solscan")

    url = f"https://pro-api.solscan.io/v2.0/token/meta?tokenAddress={mint}"
    sess = await get_session()

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
                "token": SOLSCAN_API_KEY,
            }

            async with sess.get(url, headers=headers) as res:
                if res.status == 200:
                    data = await res.json()
                    meta = data.get("data")
                    if meta:
                        print(f"[Solscan] ✅ {mint}")
                        return {
                            "source": "solscan",
                            "mcap": float(meta.get("marketCap", 0)),
                            "fdv": float(meta.get("fdv", 0)),
                            "liquidity": float(meta.get("liquidity", 0)),
                            "price": float(meta.get("priceUsdt", 0)),
                            "holders": int(meta.get("holder", 0)),
                            "mint": mint,
                        }
                elif res.status == 429:
                    await asyncio.sleep(2)

        except Exception as e:
            print(f"[Solscan] {mint}: {e}")

        await asyncio.sleep(RETRY_DELAY)

    return {}

async def get_birdeye_token_overview(mint: str) -> dict:
    if not has_quota("birdeye"):
        return {}

    record_request("birdeye")

    url = "https://public-api.birdeye.so/defi/token_overview"
    headers = {
        "X-API-KEY": BIRDEYE_API_KEY,
        "x-chain": "solana"
    }

    sess = await get_session()

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
           async with sess.get(url, headers=headers, params={"address": mint}) as res:
                if res.status == 200:
                    data = await res.json()
                    return format_birdeye_to_jupiter(data.get("data", {}))
                elif res.status == 429:
                    print(f"[Birdeye] Rate limited for {mint}. Waiting...")
                    await asyncio.sleep(5)

        except Exception as e:
            print(f"[Birdeye] {mint}: {e}")

        await asyncio.sleep(RETRY_DELAY)

    return {}

async def fetch_token_price_moralis(mint: str) -> float:
    """Fetch token price from Moralis API"""
    url = f"https://solana-gateway.moralis.io/token/mainnet/{mint}/price"
    
    sess = await get_session()

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json",
            }
            
            # Add API key only if it exists
            if MORALIS_API_KEY:
                headers["X-API-Key"] = MORALIS_API_KEY

            async with sess.get(url, headers=headers) as res:
                if res.status == 200:
                    data = await res.json()
                    price = float(data.get("usdPrice", 0.0))
                    print(f"[Moralis] ✅ Price for {mint}: ${price:.8f}")
                    return price
                elif res.status == 429:
                    print(f"[Moralis] Rate limited for {mint}. Waiting...")
                    await asyncio.sleep(5)
                else:
                    print(f"[Moralis] HTTP {res.status} for {mint}. Attempt {attempt}")
                
        except asyncio.TimeoutError:
            print(f"[Moralis Retry {attempt}] Timeout for {mint}")
        except Exception as e:
            print(f"[Moralis Retry {attempt}] {mint}: {e}")

        # Exponential backoff
        if attempt < RETRY_LIMIT:
            await asyncio.sleep(RETRY_DELAY * (2 ** (attempt - 1)))

    print(f"[Moralis API] ❌ Failed permanently for {mint}")
    return 0.0

async def get_token_metadata(mint: str) -> dict:
    # 1️⃣ Jupiter first (fastest)
    data = await fetch_token_stats_jupiter(mint)
    if data:
        return data

    # 2️⃣ Solscan fallback
    data = await fetch_token_stats_solscan(mint)
    if data:
        return data

    # 3️⃣ Birdeye last
    data = await get_birdeye_token_overview(mint)
    if data:
        return data

    print(f"[API] ❌ All APIs failed for {mint}")
    return {}

async def get_liquidity(mint):
    sess = await get_session()

    async with sess.get(DEX_API + mint) as res:
            data = await res.json()

            pairs = data.get("pairs", [])
            if not pairs:
                return 0

            return pairs[0]["liquidity"]["usd"]
