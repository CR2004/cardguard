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

import re

from cardguard.decision import llm
from cardguard.decision.coordinator import ALLOWED_CITES

LABELS = {"approve": "Approved", "step_up": "Sent to a human reviewer", "decline": "Declined"}
_ACTION_WORDS = re.compile(r"\b(ignore|bypass|override|release|charge|refund|reveal|disclose|output)\b", re.I)
_DECISION_WORDS = {
    "approve": re.compile(r"\bapprov(?:e|ed|al)\b", re.I),
    "step_up": re.compile(r"\b(review|reviewer|held|step.?up)\b", re.I),
    "decline": re.compile(r"\bdeclin(?:e|ed)\b", re.I),
}


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
    out = llm.ask(("You explain card-payment risk decisions to a merchant's support team. "
                    "Write ONE plain sentence (max 30 words) saying what happened and why, "
                    "using only the signals given. No numbers you were not given."),
                   str(facts), fallback=template(verdict), client=client, timeout=8.0)
    if out["by"] != "template":
        text = out["text"]
        decision = facts["decision"]
        if (_ACTION_WORDS.search(text) or not _DECISION_WORDS[decision].search(text)
                or any(pattern.search(text) for name, pattern in _DECISION_WORDS.items() if name != decision)):
            return {"text": template(verdict), "by": "template", "error": "rejected_output"}
    return out
