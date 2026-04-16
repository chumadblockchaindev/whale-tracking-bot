import aiohttp

session = None

async def get_session():
    """Get or create aiohttp session with proper timeout configuration"""
    global session
    if session is None or session.closed:
        # Create timeout object (FIXED!)
        timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=5)
        session = aiohttp.ClientSession(timeout=timeout)
    return session


async def close_session():
    """Close the global aiohttp session"""
    global session
    if session is not None and not session.closed:
        await session.close()
        session = None
        print("[Session] Closed aiohttp session")