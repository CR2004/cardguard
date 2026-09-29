"""What every processor shares: letters-only ids, one-use scoped verifications, a chained audit."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from cardguard.decision import audit
from cardguard.decision.guard import HEX_TO_LETTERS, TOKEN_RE
from cardguard.payment_processing.errors import IssuerReject

VERIFICATION_TTL = 3600.0  # unused verifications are forgotten after an hour


class ProcessorBase:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.verifications: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.lock = threading.Lock()  # one-use claims and balance changes are atomic

    def _card_ref(self, key: bytes, secret: str) -> str:
        ref = "tok_" + hmac.new(key, ("ref|" + secret).encode(), hashlib.sha256).hexdigest()[:16].translate(HEX_TO_LETTERS)
        if not TOKEN_RE.match(ref):  # cannot happen (hex -> letters), but never trust it silently
            raise RuntimeError("card reference failed its own format check")
        return ref

    def _new_verification(self, merchant_id: str, amount_cents: int, **extra) -> str:
        self._prune()
        vid = "ver_" + secrets.token_hex(8).translate(HEX_TO_LETTERS)  # letters-only: never card-like
        self.verifications[vid] = {"amount": int(amount_cents), "merchant": merchant_id, "used": False,
                                   "t": self.clock(), **extra}
        return vid

    def _take(self, vid: str, merchant_id: str | None, event: str) -> dict:
        """Claim a verification exactly once, for the merchant that created it (atomic)."""
        with self.lock:
            v = self.verifications.get(vid)
            if v is None or v["used"]:
                return self._reject(event, v["merchant"] if v else "?", "unknown or already used verification")
            if merchant_id is not None and v["merchant"] != merchant_id:
                return self._reject(event, merchant_id, "verification belongs to another merchant")
            v["used"] = True
            return v

    def _prune(self) -> None:
        now = self.clock()
        for vid in [k for k, v in self.verifications.items() if v["used"] or now - v["t"] > VERIFICATION_TTL]:
            del self.verifications[vid]

    def _log(self, event, merchant_id, status, **extra):
        audit.append(self.audit, {"t": self.clock(), "event": event, "merchant": merchant_id, "status": status, **extra})
        if len(self.audit) > 5000:
            audit.trim(self.audit, 5000)

    def _reject(self, event, merchant_id, reason):
        self._log(event, merchant_id, "rejected", reason=reason)
        raise IssuerReject(reason)

    def verify_audit(self) -> tuple[bool, int | None]:
        return audit.verify(self.audit)
