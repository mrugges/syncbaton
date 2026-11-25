"""One-shot HTTP requests over ``http.client``, used for Syncthing, ntfy and webhooks."""

import http.client
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> HttpResponse:
    """Send one request. Raises ``OSError`` or ``http.client.HTTPException`` on transport errors."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        msg = f"unsupported URL {url!r}"
        raise ValueError(msg)
    connection_class = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = connection_class(parts.hostname, parts.port, timeout=timeout)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        conn.request(method, target, body=body, headers=headers or {})
        response = conn.getresponse()
        return HttpResponse(response.status, response.read())
    finally:
        conn.close()
