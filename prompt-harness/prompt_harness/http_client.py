"""共享 HTTP 连接池（aiohttp）。"""
from __future__ import annotations

import aiohttp

_shared_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    """获取共享 HTTP session（自动创建，keep-alive 复用）。"""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, force_close=False)
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        _shared_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _shared_session


async def get_session_short_timeout() -> aiohttp.ClientSession:
    """获取共享 session，但覆盖为短超时（用于 embedding 等轻量请求）。"""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, force_close=False)
        timeout = aiohttp.ClientTimeout(total=120, connect=30)
        _shared_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _shared_session


async def close_session() -> None:
    """关闭共享 HTTP session（在 server shutdown 时调用）。"""
    global _shared_session
    if _shared_session is not None and not _shared_session.closed:
        await _shared_session.close()
    _shared_session = None
