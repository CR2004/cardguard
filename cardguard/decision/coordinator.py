"""Risk coordinator: Jev (TypeSafe System One) + deterministic rules.

Jev only ever sees guard-approved banded facts (never raw customer text,
tokens or payment IDs); the guide notes Jev is not adversary-hardened, so
it must not see attacker-controlled content.

The final verdict is computed in code:
  - a failed card security code check is a hard decline before any vote
  - rules and Jev each vote; the more cautious verdict wins
  - an 'approve' with Jev confidence below MIN_APPROVE_CONFIDENCE becomes 'step_up'
  - no TYPESAFE_API_KEY, or Jev errors -> rules alone decide (the verdict records decided_by and jev_error)
"""
from __future__ import annotations

import os

from cardguard.decision.guard import WireViolation, strip_for_wire

SEVERITY = {"approve": 0, "step_up": 1, "decline": 2}
MIN_APPROVE_CONFIDENCE = 0.8
JEV_ATTEMPTS = 2  # one retry on a malformed or failed answer, then rules decide alone

# Facts that decline on their own. A failed CVC check means whoever entered the
# card did not have the printed code; that is a stolen number, not a typo.
HARD_DECLINE = {("cvc_check", "fail")}

WEIGHTS = {
    ("amount_band", "medium"): 1, ("amount_band", "high"): 2,
    ("country_mismatch", "yes"): 2,
    ("velocity_band", "medium"): 1, ("velocity_band", "high"): 2,
    ("card_funding", "prepaid"): 1,
    ("cvc_check", "fail"): 3,
    ("cvc_check", "unavailable"): 1,  # issuer did not check the code; not the same as a pass
    ("new_customer", "yes"): 1,
    # model band confirms other signals rather than double-counting them
    ("model_risk_band", "medium"): 1, ("model_risk_band", "high"): 2,
    # the network agent's view: the same card at several merchants within minutes is card testing
    ("network_velocity_band", "medium"): 1, ("network_velocity_band", "high"): 5,
    # four small models, one per signal family (transaction, identity, geography, behavior), stacked;
    # same weight as the model band it partly overlaps, so it confirms rather than double-counts
    ("specialist_stack_band", "medium"): 1, ("specialist_stack_band", "high"): 2,
}

FACT_MEANINGS = {
    "amount_band": "relative to this merchant's usual order size: low below its median, medium up to its 90th percentile, high above",
    "country_mismatch": "card issuing country differs from buyer's IP country",
    "card_funding": "credit, debit or prepaid card",
    "cvc_check": "whether the card security code check passed",
    "velocity_band": "purchases with this card in the last 24h",
    "new_customer": "first time this merchant has seen this card",
    "model_risk_band": "fraud risk from a federated model trained across merchants",
    "network_velocity_band": "how many different merchants saw this same card in the last ten minutes: low one, medium two, high three or more",
    "specialist_stack_band": "fraud risk from four small models each trained on one signal family, transaction, identity, geography and behavior, and combined",
}


# Every reason a verdict may ever cite. explain.py drops anything else before a model sees it.
ALLOWED_CITES = {f"{k}={v}" for (k, v) in [*WEIGHTS, *HARD_DECLINE]}


class Verifier:
    """Receiving-side guard for the coordinator: re-checks every incoming fact set against the
    wire schema and scanner (never trusts the sender's guard) and accepts one per decision."""

    def __init__(self):
        self.accepted: dict[tuple, dict] = {}
        self.rejected: list[dict] = []

    def accept(self, decision_id: str, purpose: str, facts: dict) -> dict:
        key = (decision_id, purpose)
        try:
            if key in self.accepted:
                raise WireViolation("duplicate disclosure for this decision")
            clean = strip_for_wire(facts)
        except WireViolation as e:
            self.rejected.append({"decision_id": decision_id, "purpose": purpose, "reason": str(e)})
            raise
        self.accepted[key] = clean
        return clean


def hard_decline(facts: dict) -> list[str]:
    """Cites for facts that decline the payment regardless of any vote."""
    return [f"{k}={v}" for (k, v) in HARD_DECLINE if facts.get(k) == v]


def rules(facts: dict) -> dict:
    hard = hard_decline(facts)
    if hard:
        return {"decision": "decline", "score": None, "cites": hard, "hard": True}
    cites = [f"{k}={v}" for (k, v) in WEIGHTS if facts.get(k) == v]
    score = sum(WEIGHTS[(k, v)] for (k, v) in WEIGHTS if facts.get(k) == v)
    decision = "decline" if score >= 8 else "step_up" if score >= 3 else "approve"
    return {"decision": decision, "score": score, "cites": cites}


def _jev_state(facts: dict) -> dict:
    visible = {k: v for k, v in facts.items() if k in FACT_MEANINGS}  # drops the token
    return {"transaction_facts": visible,
            "fact_meanings": {k: FACT_MEANINGS[k] for k in visible}}


def ask_jev(facts: dict, client) -> dict:
    from typesafe_sdk import Choice, Noul, Score

    resp = client.system_one(
        state=_jev_state(facts),
        questions={
            "action": Choice(
                instructions="What should happen to this card payment",
                criteria={
                    "approve": "Signals look like a normal purchase",
                    "step_up": "Some risk signals; a human should review before charging",
                    "decline": "Strong or multiple independent fraud signals",
                }),
            "fraud_risk": Score(
                instructions="How likely this payment is fraudulent",
                criteria=["Looks legitimate", "Some suspicious signals", "Very likely fraud"]),
            "card_testing": Noul(
                instructions="Signals match card testing: many rapid small purchases or failed security checks"),
        },
        **({"model": os.environ["JEV_MODEL"]} if os.environ.get("JEV_MODEL") else {}),
    )
    a = resp.answers
    return {"model": resp.model, "action": a["action"].choice,
            "confidence": round(a["action"].confidence, 3),
            "probabilities": {k: round(v, 3) for k, v in a["action"].probabilities.items()},
            "fraud_risk": round(a["fraud_risk"].score, 3),
            "card_testing": round(a["card_testing"].noul, 3)}


class MalformedAnswer(Exception):
    """Jev replied, but not with a usable vote."""


def _check_jev(jev: dict) -> dict:
    if jev.get("action") not in SEVERITY:
        raise MalformedAnswer(f"action {jev.get('action')!r}")
    conf = jev.get("confidence")
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise MalformedAnswer(f"confidence {conf!r}")
    return jev


def _ask_jev_with_retry(facts: dict, client) -> tuple[dict | None, list[str]]:
    """Returns (answer or None, error names per attempt)."""
    errors = []
    for _ in range(JEV_ATTEMPTS):
        try:
            return _check_jev(ask_jev(facts, client)), errors
        except Exception as e:  # network, SDK, or malformed: retry once, then give up
            errors.append(type(e).__name__)
    return None, errors


def _default_client():
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None
    from typesafe_sdk import TypeSafeClient
    return TypeSafeClient(timeout=5.0)


def decide(facts: dict, client=None) -> dict:
    facts = strip_for_wire(facts)  # defense in depth: no unguarded value can ever reach a model
    verdict = rules(facts)
    if verdict.get("hard"):  # nothing to vote on; Jev is not even asked
        return {**verdict, "decided_by": "rules"}
    client = client if client is not None else _default_client()
    if client is None:
        return {**verdict, "decided_by": "rules"}
    jev, errors = _ask_jev_with_retry(facts, client)
    if jev is None:  # never block payments on the model being down or confused
        return {**verdict, "decided_by": "rules", "jev_error": errors[-1], "jev_attempts": errors}

    final = max(verdict["decision"], jev["action"], key=SEVERITY.__getitem__)
    gated = final == "approve" and jev["confidence"] < MIN_APPROVE_CONFIDENCE
    if gated:
        final = "step_up"
    out = {**verdict, "decision": final, "rules_decision": verdict["decision"],
           "jev": jev, "confidence_gated": gated, "decided_by": "rules+jev"}
    if errors:
        out["jev_retries"] = errors
    return out
