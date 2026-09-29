"""Wire guard: the only path by which data leaves the merchant node.

Two layers, both fail closed:
1. Allowlist schema - only known keys with known banded values may cross.
2. Leak scanner - rejects anything that looks like a card number (Luhn-valid
   13-19 digits, spaces/dashes allowed), a CVV field, or an expiry date,
   even inside an allowed field.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from cardguard.decision import audit

# Closed vocabulary: key -> allowed values. Nothing else crosses.
WIRE_SCHEMA: dict[str, set[str]] = {
    "token": set(),  # special-cased: must match TOKEN_RE
    "amount_band": {"low", "medium", "high"},
    "country_mismatch": {"yes", "no"},
    "card_funding": {"credit", "debit", "prepaid", "unknown"},
    "cvc_check": {"pass", "fail", "unavailable"},
    "velocity_band": {"low", "medium", "high"},
    "new_customer": {"yes", "no"},
    "model_risk_band": {"low", "medium", "high"},
    "network_velocity_band": {"low", "medium", "high"},  # set by the coordinator: same card at 1 / 2 / 3+ merchants in 10 min
    "specialist_stack_band": {"low", "medium", "high"},  # four one-signal-family models (FedAvg across merchants), stacked
    # the bank's attestations: round 1 about the cardholder, round 2 about travel (never a place or a time)
    "issuer_behavior": {"low", "medium", "high", "unknown"},
    "issuer_recent_declines": {"none", "some", "many", "unknown"},
    "travel_check": {"plausible", "implausible", "unknown"},
}
TOKEN_RE = re.compile(r"^tok_[a-p]{16}$")  # letters only: can never resemble a card number
HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")  # for every letters-only id we mint

_DIGIT_RUN = re.compile(r"(?:\d[ -]?){13,19}")
_EXPIRY = re.compile(r"\b(0[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})\b")
_CVV_KEY = re.compile(r"cvv|cvc|csc|security.?code", re.I)


class WireViolation(Exception):
    pass


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def find_leaks(text: str, cvv_words: bool = True) -> list[str]:
    """Return reasons text would leak card data (empty list = clean).
    cvv_words=False scans model OUTPUT shown to people (digits and expiry only): a sentence may
    legitimately say "the security code check passed". The wire always uses the full scan."""
    reasons = []
    for m in _DIGIT_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            reasons.append("card-number-like digits")
    if _EXPIRY.search(text):
        reasons.append("expiry-date-like value")
    if cvv_words and _CVV_KEY.search(text):
        reasons.append("cvv reference")
    return reasons


def json_values(obj):
    """Every leaf of a JSON-like object as a string, ignoring key names (keys are schema-checked)."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from json_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from json_values(v)
    else:
        yield str(obj)


def strip_for_wire(payload: dict) -> dict:
    """Validate a payload against the schema and scanner. Raises on anything odd."""
    out = {}
    for key, value in payload.items():
        if key not in WIRE_SCHEMA:
            raise WireViolation("unknown key")  # the name is attacker-controlled: never echo it
        if not isinstance(value, str) or len(value) > 40:
            raise WireViolation(f"bad value type/size for {key!r}")
        leaks = find_leaks(value)  # keys are allowlisted; scan values only
        if leaks:
            raise WireViolation(f"{key!r}: {', '.join(leaks)}")
        if key == "token":
            if not TOKEN_RE.match(value):
                raise WireViolation("malformed token")
        elif value not in WIRE_SCHEMA[key]:
            raise WireViolation(f"value for {key!r} not in vocabulary")  # the value is never echoed
        out[key] = value
    return out


@dataclass
class Ledger:
    """Every disclosure and every blocked attempt, with source and purpose. Bounded in memory:
    beyond MAX_ENTRIES the oldest entries are dropped and the chain continues from an anchor."""
    entries: list[dict] = field(default_factory=list)
    anchor: str = audit.GENESIS
    MAX_ENTRIES: int = 5000

    def _append(self, entry: dict) -> dict:
        stored = audit.append(self.entries, entry, self.anchor)
        if len(self.entries) > self.MAX_ENTRIES:
            self.anchor = audit.trim(self.entries, self.MAX_ENTRIES) or self.anchor
        return stored

    def disclose(self, source: str, purpose: str, payload: dict) -> dict:
        try:
            clean = strip_for_wire(payload)
        except WireViolation as e:
            self._append({"t": time.time(), "source": source, "purpose": purpose,
                          "status": "BLOCKED", "reason": str(e)})
            raise
        self._append({"t": time.time(), "source": source, "purpose": purpose,
                      "status": "DISCLOSED", "fields": clean})
        return clean

    def verify_chain(self) -> tuple[bool, int | None]:
        """Tamper check: every entry hashes the previous one, from the anchor."""
        return audit.verify(self.entries, self.anchor)

    def card_numbers_seen_by_coordinator(self) -> int:
        """Audit: re-scan every DISCLOSED field for card-like values. The guard makes this 0 by
        construction; the number exists so anyone can check that claim against the log itself."""
        return sum(1 for e in self.entries if e["status"] == "DISCLOSED"
                   and any(find_leaks(v) for v in e["fields"].values()))
