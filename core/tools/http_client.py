from __future__ import annotations

from urllib.parse import urlparse
from typing import Optional, Set


class HttpClient:
    def __init__(self, allowlist: Optional[Set[str]] = None, timeout_seconds: float = 5.0) -> None:
        self.allowlist = allowlist or {"example.com"}
        self.timeout_seconds = timeout_seconds

    def validate_url(self, url: str) -> str:
        host = urlparse(url).hostname or ""
        if host not in self.allowlist:
            raise PermissionError(f"domain not allowlisted: {host}")
        return host

    def get(self, url: str) -> dict:
        self.validate_url(url)
        return {"url": url, "status": 200, "body": "mock response"}
