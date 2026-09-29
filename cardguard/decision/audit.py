"""Hash-chained, append-only logs. Each entry carries the hash of the previous one, so removing,
reordering or editing any entry breaks every hash after it. Used by the merchant ledger and the
processor audit log.

With AUDIT_KEY set (hex), hashes are HMAC-SHA256 under that key, so a writer without the key
cannot recompute a consistent chain. A capped log keeps the hash of the last dropped entry as its
anchor and verifies from there; anchoring the head off-node (signing, publishing) is deployment work.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading

GENESIS = "0" * 64
_KEY = bytes.fromhex(os.environ["AUDIT_KEY"]) if os.environ.get("AUDIT_KEY") else b""
_LOCK = threading.Lock()


def entry_hash(entry: dict) -> str:
    body = json.dumps({k: v for k, v in entry.items() if k != "hash"}, sort_keys=True,
                      separators=(",", ":"), default=str).encode()
    return hmac.new(_KEY, body, hashlib.sha256).hexdigest() if _KEY else hashlib.sha256(body).hexdigest()


def append(entries: list[dict], entry: dict, anchor: str = GENESIS) -> dict:
    """Append `entry` with seq, prev and hash filled in (thread-safe). Returns the stored entry."""
    with _LOCK:
        prev = entries[-1]["hash"] if entries else anchor
        seq = entries[-1]["seq"] + 1 if entries else 0
        entry = {**entry, "seq": seq, "prev": prev}
        entry["hash"] = entry_hash(entry)
        entries.append(entry)
        return entry


def reseal(entry: dict) -> None:
    """Recompute an entry's hash after adding local context to it (only valid for the last entry)."""
    entry["hash"] = entry_hash(entry)


def verify(entries: list[dict], anchor: str = GENESIS) -> tuple[bool, int | None]:
    """(True, None) if the chain is intact from `anchor`, else (False, index of the first broken entry)."""
    prev = anchor
    seq = entries[0]["seq"] if entries else 0
    for i, e in enumerate(entries):
        if e.get("seq") != seq + i or e.get("prev") != prev or e.get("hash") != entry_hash(e):
            return False, i
        prev = e["hash"]
    return True, None


def head(entries: list[dict], anchor: str = GENESIS) -> str:
    return entries[-1]["hash"] if entries else anchor


def trim(entries: list[dict], keep: int) -> str:
    """Drop the oldest entries beyond `keep`; return the anchor (hash of the last dropped entry)."""
    if len(entries) <= keep:
        return None  # type: ignore[return-value]
    dropped = entries[: len(entries) - keep]
    del entries[: len(entries) - keep]
    return dropped[-1]["hash"]
