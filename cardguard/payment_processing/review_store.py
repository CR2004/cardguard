"""Human-in-the-loop bookkeeping for the merchant node: the closed reason vocabulary, opinion
validation, persisted training labels, the hash-chained review audit, and reviewer metrics.

Everything here stays on this node. Free-text notes are scanned for card-like data, then only their
length and hash are audited; the note never reaches the coordinator, a model, or Ledger.disclose().
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import threading

from cardguard.decision import audit
from cardguard.decision.guard import find_leaks

REASONS = ("card_testing", "ring_pattern", "known_customer", "customer_verified",
           "amount_out_of_pattern", "other")
NOTE_MAX = 200
MAX_LABELS = 2000
_REVIEWER_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
MODEL_SAYS_FRAUD = {"medium", "high"}


def parse_opinion(body, reviewer_header: str | None) -> tuple[dict | None, str | None]:
    """Validate the optional JSON body and X-Reviewer-Id. Returns (opinion, None) or (None, error)."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    reason = body.get("reason")
    if reason is not None and reason not in REASONS:
        return None, "reason must be one of: " + ", ".join(REASONS)
    note = body.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            return None, f"note must be text of at most {NOTE_MAX} characters"
        if find_leaks(note):
            return None, "note looks like it contains card data; it was not saved"
    reviewer = None
    if reviewer_header is not None:
        if not _REVIEWER_ID.fullmatch(reviewer_header):
            return None, "X-Reviewer-Id must be 1-32 letters, digits, - or _"
        reviewer = reviewer_header
    return {"reason": reason, "note": note or "", "reviewer": reviewer}, None


def _read_jsonl(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a torn or corrupt line is skipped, never fatal
    except OSError:
        pass
    return out


class LabelStore:
    """Labels (local feature vector, 0/1) persisted as JSONL: features, label, source, reason,
    decision_id, t. Never holds card data. A missing or corrupt file just means fewer labels."""

    def __init__(self, path: str, n_features: int):
        self.path, self.n_features, self._lock = path, n_features, threading.Lock()
        self._lines = 0

    def load(self) -> list[tuple[list[float], int]]:
        rows = []
        for r in _read_jsonl(self.path):
            f, lab = (r.get("features"), r.get("label")) if isinstance(r, dict) else (None, None)
            if (isinstance(f, list) and len(f) == self.n_features and lab in (0, 1)
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in f)):
                rows.append((list(map(float, f)), int(lab)))
        self._lines = len(rows)
        return rows[-MAX_LABELS:]

    def append(self, features, label: int, source: str, reason, decision_id, now: float) -> None:
        rec = {"features": [float(x) for x in features], "label": int(label), "source": source,
               "reason": reason, "decision_id": decision_id, "t": now}
        try:
            with self._lock:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                self._lines += 1
                if self._lines > 2 * MAX_LABELS:
                    self._compact()
        except OSError:
            pass  # persistence is best effort: the in-memory label still counts

    def _compact(self) -> None:
        keep = _read_jsonl(self.path)[-MAX_LABELS:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in keep)
        os.replace(tmp, self.path)
        self._lines = len(keep)


class ReviewAudit:
    """Hash-chained record of every human opinion, persisted as JSONL. Holds no card data and no
    note text (only its length and a hash). A file whose chain does not verify is moved aside."""

    def __init__(self, path: str):
        self.path, self.load_error = path, None
        self.entries: list[dict] = []
        rows = [r for r in _read_jsonl(path) if isinstance(r, dict)]
        if rows:
            ok, _ = audit.verify(rows)
            if ok:
                self.entries = rows
            else:
                self.load_error = "review audit chain failed verification; moved aside, starting a new chain"
                try:
                    os.replace(path, path + ".corrupt")
                except OSError:
                    pass

    def record(self, entry: dict) -> dict:
        stored = audit.append(self.entries, entry)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(stored, default=str) + "\n")
        except OSError:
            pass
        return stored

    def verify(self) -> bool:
        return audit.verify(self.entries)[0]


def opinion_entry(rid: str, item: dict, action: str, outcome: str, op: dict, now: float,
                  first_reviewer: str | None = None) -> dict:
    """The audit record for one human opinion: what the reviewer was shown and what they said."""
    note = op["note"].encode()
    facts = item.get("facts") or {}
    return {"kind": "opinion", "review_id": rid, "decision_id": item.get("decision_id"),
            "suspected": item.get("kind"), "decision": action, "outcome": outcome,
            "reason": op["reason"], "reviewer": op["reviewer"], "first_reviewer": first_reviewer,
            "model_band": facts.get("model_risk_band"), "cites": list(item.get("cites", [])),
            "note_len": len(note), "note_sha256": hashlib.sha256(note).hexdigest() if note else None,
            "queued_at": item.get("t"), "t": now,
            "seconds_to_review": round(now - item["t"], 1) if item.get("t") else None}


def expiry_entry(rid: str, item: dict, now: float) -> dict:
    return {"kind": "expired", "review_id": rid, "decision_id": item.get("decision_id"),
            "suspected": item.get("kind"), "outcome": "expired_voided", "queued_at": item.get("t"), "t": now}


def stats(entries: list[dict], pending: dict, now: float) -> dict:
    final = [e for e in entries if e.get("kind") == "opinion" and e.get("outcome") in {"approved", "declined"}]
    rated = [e for e in final if e.get("model_band") is not None]
    agree = [e for e in rated if (e["model_band"] in MODEL_SAYS_FRAUD) == (e["decision"] == "decline")]
    soft = [e for e in final if e.get("suspected") == "soft_decline"]
    secs = [e["seconds_to_review"] for e in final if e.get("seconds_to_review") is not None]
    ages = [now - v["t"] for v in pending.values() if v.get("t")]
    by_reason: dict[str, int] = {}
    for e in final:
        by_reason[e.get("reason") or "none"] = by_reason.get(e.get("reason") or "none", 0) + 1
    return {"reviews_done": len(final),
            "queue_length": len(pending), "oldest_age_seconds": round(max(ages), 1) if ages else None,
            "model_human_agreement": round(len(agree) / len(rated), 3) if rated else None,
            "soft_declines_reviewed": len(soft),
            "soft_decline_overturn_rate": round(sum(e["decision"] == "approve" for e in soft) / len(soft), 3) if soft else None,
            "median_seconds_to_review": round(statistics.median(secs), 1) if secs else None,
            "expired_voided": sum(e.get("kind") == "expired" for e in entries),
            "by_reason": by_reason}
