from functools import lru_cache
import ssl

import httpx


@lru_cache
def shared_ssl_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()
