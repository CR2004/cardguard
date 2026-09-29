"""Live investigation trace: one ordered list of steps per checkout, for the checkout page.

Display only. Nothing here feeds a decision. Every value is a band from the wire vocabulary, a
count, an id the SuperLink assigned, or a fixed reason string; each event is leak-scanned before it
is stored, and the full card reference is masked. The page polls GET /trace/<id> and animates only
the events that have actually happened, so the picture can never run ahead of the system.
"""
from __future__ import annotations

import collections
import re
import threading
import time

from cardguard.decision import coordinator as coord
from cardguard.decision.guard import find_leaks, json_values

TRACE_ID_RE = re.compile(r"^[a-p]{16}$")   # minted by the page, letters only like every id we show
NODE_ID_RE = re.compile(r"^\d{1,20}$")      # SuperNode and run ids come from the SuperLink
ID_KEYS = {"run_id", "node", "nodes"}       # our own ids: validated by shape, not leak-scanned
MAX_TRACES, MAX_EVENTS = 50, 200

# Which party a fact comes from. Stripe's card checks and the bank's attestations reach the coordinator
# inside the store's disclosure (the bank's round-2 answer comes back through the store's node); the
# network fact is the coordinator's own memory of other stores' sightings.
PARTY_OF = {"cvc_check": "stripe", "card_funding": "stripe",
            "issuer_behavior": "bank", "issuer_recent_declines": "bank", "travel_check": "bank",
            "amount_band": "store", "country_mismatch": "store", "velocity_band": "store",
            "new_customer": "store", "model_risk_band": "store", "specialist_stack_band": "store",
            "network_velocity_band": "network"}


def _mask(token: str) -> str:
    return token[:6] + "…" + token[-3:] if isinstance(token, str) and token.startswith("tok_") else token


def _ids_ok(value) -> bool:
    values = value if isinstance(value, list) else [value]
    return all(NODE_ID_RE.match(str(v)) for v in values)


class Trace:
    def __init__(self, trace_id: str, clock=time.time):
        self.id, self.clock = trace_id, clock
        self.start = clock()
        self.events: list[dict] = []
        self.done = False
        self._lock = threading.Lock()

    def add(self, kind: str, **fields) -> dict:
        """Append one step. A card-like value anywhere drops the event's content, never the fact
        that something happened."""
        ids = {k: fields.pop(k) for k in list(fields) if k in ID_KEYS}
        ids = {k: (list(map(str, v)) if isinstance(v, list) else str(v)) for k, v in ids.items() if _ids_ok(v)}
        if "evidence" in fields and isinstance(fields["evidence"], dict):
            fields["evidence"] = {k: _mask(v) if k == "token" else v for k, v in fields["evidence"].items()}
        if any(find_leaks(v, cvv_words=False) for v in json_values(fields)):
            kind, fields = "redacted", {"detail": "a card-like value was dropped from this step"}
        with self._lock:
            event = {"seq": len(self.events), "t": round((self.clock() - self.start) * 1000, 1),
                     "kind": kind, **ids, **fields}
            if len(self.events) < MAX_EVENTS:
                self.events.append(event)
        return event

    def since(self, after: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > after]


class TraceStore:
    """Bounded: the page only ever needs its own recent checkouts."""

    def __init__(self):
        self.traces: collections.OrderedDict[str, Trace] = collections.OrderedDict()
        self.by_decision: dict[str, Trace] = {}
        self._lock = threading.Lock()

    def open(self, trace_id: str | None) -> Trace | None:
        if not trace_id or not TRACE_ID_RE.match(str(trace_id)):
            return None
        with self._lock:
            if trace_id in self.traces:  # one checkout per trace: a reused id never writes into another's story
                return None
            trace = Trace(trace_id)
            self.traces[trace_id] = trace
            while len(self.traces) > MAX_TRACES:
                _, old = self.traces.popitem(last=False)
                for d in [d for d, t in self.by_decision.items() if t is old]:
                    self.by_decision.pop(d, None)
            return trace

    def get(self, trace_id: str) -> Trace | None:
        with self._lock:
            return self.traces.get(trace_id)

    def bind(self, decision_id: str, trace: Trace | None) -> None:
        if trace is not None:
            with self._lock:
                self.by_decision[decision_id] = trace

    def for_decision(self, decision_id: str) -> Trace | None:
        with self._lock:
            return self.by_decision.get(decision_id)


# ---------------------------------------------------------------- how the verdict was reached

def party_view(facts: dict) -> list[dict]:
    """Each party's own signal, from the policy's own weights (display only; the gate uses the total)."""
    parties: dict[str, dict] = {}
    for key, value in facts.items():
        if key == "token" or key not in PARTY_OF:
            continue
        p = parties.setdefault(PARTY_OF[key], {"party": PARTY_OF[key], "facts": {}, "points": 0, "hard": False,
                                                "floor": False})
        p["facts"][key] = value
        p["points"] += coord.WEIGHTS.get((key, value), 0)
        p["hard"] = p["hard"] or (key, value) in coord.HARD_DECLINE
        p["floor"] = p["floor"] or (key, value) in coord.REVIEW_FLOOR
    for p in parties.values():
        if p["hard"]:
            p["level"] = "high"
        elif p["floor"]:
            p["level"] = "medium" if p["points"] <= 4 else "high"
        elif p["party"] == "network":
            p["level"] = p["facts"].get("network_velocity_band", "low")
        else:
            p["level"] = "low" if p["points"] <= 1 else "medium" if p["points"] <= 4 else "high"
    return [parties[k] for k in ("stripe", "bank", "store", "network") if k in parties]


def gate_report(verdict: dict, facts: dict) -> dict:
    """The fixed rules, line by line, as they applied to this verdict. Reads the verdict; never re-decides."""
    lines: list[dict] = []
    decision = verdict.get("decision", "step_up")

    def line(rule: str, state: str, text: str) -> None:
        lines.append({"rule": rule, "state": state, "text": text})

    if verdict.get("decided_by") == "no_facts":
        line("evidence", "fail", "No verified evidence arrived, so automation cannot approve")
    else:
        line("evidence", "pass", "Evidence complete: one verified fact set for this decision")
        line("privacy", "pass", "Privacy check passed: closed vocabulary, no card-like values")
        if verdict.get("hard"):
            line("hard_stop", "fail", "CVC check failed: automatic decline, no vote")
        else:
            line("hard_stop", "pass", "No hard stop: the CVC check did not fail")
            score = verdict.get("score") or 0
            rules_decision = verdict.get("rules_decision", decision if verdict.get("decided_by") == "rules" else None)
            line("score", "info", f"Risk points {score}: review from {coord.REVIEW_AT}, decline from {coord.DECLINE_AT}")
            travel = facts.get("travel_check")
            if verdict.get("round_2_unanswered"):
                line("round_2", "warn", "Round 2 got no verified answer from the bank, so a person decides")
            elif travel:
                line("round_2", {"implausible": "warn", "plausible": "pass"}.get(travel, "info"),
                     {"implausible": "Round 2: the bank finds the travel implausible, so a person must look",
                      "plausible": "Round 2: the bank finds the travel plausible; no extra caution"}.get(
                         travel, "Round 2: the bank could not say; no change"))
            if rules_decision:
                floored = rules_decision == "step_up" and score < coord.REVIEW_AT
                line("rules", {"approve": "pass", "step_up": "warn", "decline": "fail"}[rules_decision],
                     "Below the review line, but round-2 evidence puts a person in the loop" if floored else
                     {"approve": "Below the review line: rules approve",
                      "step_up": "Auto-decline line not reached: rules send it to a person",
                      "decline": "Past the decline line: rules decline"}[rules_decision])
            jev = verdict.get("jev")
            if jev:
                line("model", "info", f"Model vote: {jev['action'].replace('_', ' ')} at "
                                      f"{round(jev['confidence'] * 100)}% confidence; the more cautious vote wins")
                if verdict.get("confidence_gated"):
                    line("confidence", "warn", f"Approve below {round(coord.MIN_APPROVE_CONFIDENCE * 100)}% "
                                               "model confidence: a person decides")
            elif verdict.get("jev_error"):
                line("model", "info", "Model vote unavailable: rules decide alone")
            else:
                line("model", "info", "No model vote configured: rules decide alone")
    contributions = [{"fact": f"{k}={v}", "points": coord.WEIGHTS.get((k, v), 0), "party": PARTY_OF.get(k, "store")}
                     for (k, v) in [c.split("=", 1) for c in verdict.get("cites", []) if "=" in c]]
    return {"decision": decision, "decided_by": verdict.get("decided_by"), "score": verdict.get("score"),
            "lines": lines, "contributions": contributions, "parties": party_view(facts),
            "round_2": "travel_check" in facts or bool(verdict.get("round_2_unanswered"))}


# ---------------------------------------------------------------- Flower run events -> trace

def record_flower_event(trace: Trace, kind: str, payload: dict) -> None:
    """Map the coordinator's run events (streamed by the launcher) to trace steps."""
    if kind == "run.started":
        trace.add("flower.run.started", src="store", dst="coordinator", run_id=payload.get("run_id"))
    elif kind == "run.finished":
        trace.add("flower.run.finished", run_id=payload.get("run_id"))
    elif kind == "cardguard.step":
        step = payload.get("step")
        if step == "nodes":
            trace.add("coord.nodes", src="coordinator", nodes=payload.get("nodes", []))
        elif step == "conflict":
            trace.add("coord.conflict", src="coordinator", round=payload.get("round", 2), reason=payload.get("reason"))
        elif step == "question":
            trace.add("coord.question", src="coordinator", dst="store", round=payload.get("round", 1),
                      nodes=payload.get("to", []), bytes=payload.get("bytes"),
                      purpose=payload.get("purpose", "fraud-risk"), via="flower")
        elif step == "reply":
            trace.add("coord.reply", src="store", dst="coordinator", round=payload.get("round", 1),
                      node=payload.get("node"), status=payload.get("status"), reason=payload.get("reason"),
                      bytes=payload.get("bytes"), latency_ms=payload.get("latency_ms"), via="flower",
                      evidence=payload.get("facts") if isinstance(payload.get("facts"), dict) else None)
        elif step == "network":
            trace.add("coord.network", src="network", dst="coordinator",
                      evidence={"network_velocity_band": payload.get("band")})
    elif kind == "cardguard.verdict" and isinstance(payload.get("verdict"), dict):
        record_gate(trace, payload["verdict"], payload["verdict"].get("facts") or {}, via="flower")


def record_gate(trace: Trace, verdict: dict, facts: dict, via: str) -> None:
    explanation = verdict.get("explanation") if isinstance(verdict.get("explanation"), dict) else {}
    trace.add("gate.decision", src="coordinator", dst="gate", via=via, **gate_report(verdict, facts),
              explanation={"text": str(explanation.get("text", ""))[:400], "by": str(explanation.get("by", ""))[:60]})
