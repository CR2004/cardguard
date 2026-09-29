"""Network agent view: the same card reference seen at several merchants in a short window.

Each merchant alone sees one purchase with low velocity. The coordinator, which receives every
merchant's guarded facts over the Grid, sees the card reference at store A, then B, then C, and
turns a card-testing ring into a fact: network_velocity_band. It stores only (card reference,
merchant id, time): letters-only tokens, never anything about the card itself.
State lives in the Flower run series (context.state) when running as an AgentApp, so it survives
across decisions; in-process it is a module singleton.
"""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)

WINDOW = 600.0  # seconds: "within ten minutes"
MERCHANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9:-]{0,63}$")  # "<node id>:<store>" or a bare id


class NetworkWatch:
    def __init__(self, rows: list[list] | None = None):
        self.rows: list[list] = [list(r) for r in (rows or [])]  # [token, merchant_id, t]

    def observe(self, token: str, merchant_id: str, now: float | None = None) -> tuple[str, list[str]]:
        """Record this sighting and return (band, merchants that saw this card in the window)."""
        now = time.time() if now is None else now
        if not MERCHANT_ID_RE.match(merchant_id):
            raise ValueError("bad merchant id")
        self.rows = [r for r in self.rows if now - r[2] < WINDOW]
        self.rows.append([token, merchant_id, now])
        merchants = sorted({r[1] for r in self.rows if r[0] == token})
        band = "low" if len(merchants) <= 1 else "medium" if len(merchants) == 2 else "high"
        return band, merchants

    def to_json(self) -> str:
        return json.dumps(self.rows, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | None) -> "NetworkWatch":
        try:
            return cls(json.loads(text) if text else [])
        except (ValueError, TypeError):
            return cls()


STATE_KEY = "cardguard_network"


def load_from_context(context) -> NetworkWatch:
    """The coordinator's network table, persisted in the run series' state when available."""
    try:
        record = context.state.config_records.get(STATE_KEY)
        return NetworkWatch.from_json(record["json"] if record is not None else None)
    except Exception as e:  # noqa: BLE001 - no state (tests, fresh series): start empty, but say so
        log.warning("network table not loaded from context (%s); starting empty", type(e).__name__)
        return NetworkWatch()


def save_to_context(context, watch: NetworkWatch) -> None:
    try:
        from flwr.app import ConfigRecord
        context.state.config_records[STATE_KEY] = ConfigRecord({"json": watch.to_json()})
    except Exception as e:  # noqa: BLE001
        log.warning("network table not saved to context (%s); the next run will not remember this card", type(e).__name__)


NETWORK = NetworkWatch()  # in-process singleton (one merchant process with several stores)
