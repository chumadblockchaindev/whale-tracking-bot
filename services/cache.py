import aiosqlite
import os

CACHE_DB = "data/token_cache.db"
CACHED_MINTS = set()
USE_SQLITE = True  # Toggle to False to use in-memory only

# Initialize SQLite DB or in-memory cache
async def init_cache():
    if USE_SQLITE:
        os.makedirs("data", exist_ok=True)
        async with aiosqlite.connect(CACHE_DB) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    mint TEXT PRIMARY KEY
                )
            """)
            await db.commit()

            async with db.execute("SELECT mint FROM cache") as cursor:
                async for row in cursor:
                    CACHED_MINTS.add(row[0])
        print(f"[Cache] Loaded {len(CACHED_MINTS)} mints from SQLite.")
    else:
        print("[Cache] Using in-memory cache only.")

# Check if a mint is already cached
async def is_cached(mint: str) -> bool:
    return mint in CACHED_MINTS

# Cache a new mint
async def cache_token(mint: str):
    CACHED_MINTS.add(mint)
    if USE_SQLITE:
        try:
            async with aiosqlite.connect(CACHE_DB) as db:
                await db.execute("INSERT OR IGNORE INTO cache (mint) VALUES (?)", (mint,))
                await db.commit()
        except Exception as e:
            print(f"[Cache] Failed to cache mint {mint}: {e}")
