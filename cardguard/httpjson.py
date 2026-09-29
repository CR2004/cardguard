"""The one JSON-over-HTTP helper every client in the package uses (urllib, http(s) only, no retries)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


class HttpFailure(Exception):
    """Transport failure or HTTP error; `.status` is the HTTP status when there was one, else None."""

    def __init__(self, reason: str, status: int | None = None, body: dict | None = None):
        super().__init__(reason)
        self.status, self.body = status, body or {}


def post_json(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 5.0,
              ssl_context: ssl.SSLContext | None = None, method: str = "POST") -> dict:
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) URLs")
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:  # noqa: S310 - scheme checked above
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read())
        except Exception:  # noqa: BLE001
            parsed = {}
        raise HttpFailure(parsed.get("error", f"HTTP {e.code}"), status=e.code, body=parsed) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise HttpFailure(f"unreachable: {type(e).__name__}") from None
