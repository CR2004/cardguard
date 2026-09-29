"""One-line plain-English explanation of each verdict, written by Flower's Endeavor model.

Display only: the explanation never changes the decision.
Endeavor sees the verdict and the cited banded facts, nothing else.
Its output also goes through the leak scanner before anyone sees it.

Endpoint config (OpenAI-compatible Responses API):
  - inside a Flower AgentApp: FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY are injected
  - elsewhere: set ENDEAVOR_BASE_URL / ENDEAVOR_API_KEY
  - model id from LLM_MODEL / ENDEAVOR_MODEL (see cardguard.decision.llm for the endpoint precedence)
No endpoint configured, or any error -> a template sentence instead.
"""
from __future__ import annotations


from cardguard.decision import llm
from cardguard.decision.coordinator import ALLOWED_CITES

LABELS = {"approve": "Approved", "step_up": "Sent to a human reviewer", "decline": "Declined"}


def template(verdict: dict) -> str:
    cites = verdict.get("cites") or []
    why = ", ".join(c.replace("_", " ").replace("=", ": ") for c in cites) or "no risk signals"
    return f"{LABELS[verdict['decision']]} because of {why}."


def explain(verdict: dict, client=None) -> dict:
    facts = {"decision": verdict["decision"] if verdict["decision"] in LABELS else "step_up",
             "cited_signals": [c for c in verdict.get("cites", []) if c in ALLOWED_CITES],
             "decided_by": verdict.get("decided_by") if verdict.get("decided_by") in {"rules", "rules+jev"} else "rules"}
    if verdict.get("confidence_gated"):
        facts["note"] = "an automated model was not confident enough to approve on its own"
    return llm.ask(("You explain card-payment risk decisions to a merchant's support team. "
                    "Write ONE plain sentence (max 30 words) saying what happened and why, "
                    "using only the signals given. No numbers you were not given."),
                   str(facts), fallback=template(verdict), client=client, timeout=8.0)
