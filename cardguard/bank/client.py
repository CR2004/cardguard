"""What the merchant node needs to ask the bank: the signed request format, the coarse regions, and a
client that degrades to "unknown" when the bank is unreachable. It never imports the bank's history.

Demo correlation, not an issuer-network identity protocol: the merchant names a card by its
pseudonymous card_ref (the Stripe adapter's keyed hash of Stripe's TEST card fingerprint; the raw
fingerprint never leaves that adapter) plus the card's coarse issuing region.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

from cardguard.httpjson import HttpFailure, post_json

# Coarse regions: the only location the store ever tells the bank, and never a city.
REGIONS = {"US": "North America", "CA": "North America", "MX": "North America", "DE": "Europe", "GB": "Europe",
           "FR": "Europe", "PT": "Europe", "AT": "Europe", "BR": "Latin America", "NG": "Africa"}
REGION_NAMES = frozenset(REGIONS.values()) | {"Elsewhere"}


def region_of(country: str) -> str:
    return REGIONS.get(str(country), "Elsewhere")


def sign(secret: str, merchant_id: str, method: str, path: str, body: bytes, ts: int | None = None,
         nonce: str | None = None) -> dict:
    """Headers for a signed call: HMAC-SHA256 over timestamp, method, path, nonce and body."""
    ts = ts if ts is not None else int(time.time())
    nonce = nonce if nonce is not None else secrets.token_hex(8)
    msg = f"{ts}\n{method}\n{path}\n{nonce}\n".encode() + body
    return {"X-Merchant-Id": merchant_id, "X-Timestamp": str(ts), "X-Nonce": nonce,
            "X-Signature": hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()}


class BankClient:
    """Round 1 (attest) and round 2 (travel_check) over the signed local channel. Any failure is None:
    the bank is not a hard dependency, and the caller decides what an absent answer means."""

    def __init__(self, url: str, merchant_id: str, secret: str, timeout: float = 3.0):
        if not url.startswith(("http://", "https://")):
            raise ValueError("BANK_URL must be http(s)")
        self.url, self.merchant_id, self.secret, self.timeout = url.rstrip("/"), merchant_id, secret, timeout

    @classmethod
    def from_env(cls, merchant_id: str) -> "BankClient | None":
        url = os.environ.get("BANK_URL", "")
        return cls(url, merchant_id, os.environ.get("BANK_SECRET", "")) if url else None

    def _post(self, path: str, body: dict) -> dict | None:
        data = json.dumps(body).encode()
        try:
            return post_json(self.url + path, body, headers=sign(self.secret, self.merchant_id, "POST", path, data),
                             timeout=self.timeout)
        except HttpFailure:
            return None

    def attest(self, card_ref: str, card_region: str) -> dict | None:
        return self._post("/attest", {"card_ref": card_ref, "card_region": card_region})

    def travel_check(self, card_ref: str, card_region: str, buyer_region: str, decision_id: str) -> dict | None:
        return self._post("/travel-check", {"card_ref": card_ref, "card_region": card_region,
                                            "buyer_region": buyer_region, "decision_id": decision_id})
