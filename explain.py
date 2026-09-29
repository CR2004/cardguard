"""One-line plain-English explanation of each verdict, written by Flower's Endeavor model.

Display only: the explanation never changes the decision.
Endeavor sees the verdict and the cited banded facts, nothing else.
Its output also goes through the leak scanner before anyone sees it.

Endpoint config (OpenAI-compatible Responses API):
  - inside a Flower AgentApp: FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY are injected
  - elsewhere: set ENDEAVOR_BASE_URL / ENDEAVOR_API_KEY
  - ENDEAVOR_MODEL overrides the model id (default flower-endeavor-v1.0)
No endpoint configured, or any error -> a template sentence instead.
"""
from __future__ import annotations

import os

from guard import find_leaks

DEFAULT_MODEL = "flower-endeavor-v1.0"
LABELS = {"approve": "Approved", "step_up": "Sent to a human reviewer", "decline": "Declined"}


def template(verdict: dict) -> str:
    cites = verdict.get("cites") or []
    why = ", ".join(c.replace("_", " ").replace("=", ": ") for c in cites) or "no risk signals"
    return f"{LABELS[verdict['decision']]} because of {why}."


def _default_client():
    base = os.environ.get("FLWR_RUNTIME_BASE_URL") or os.environ.get("ENDEAVOR_BASE_URL")
    key = os.environ.get("FLWR_RUNTIME_API_KEY") or os.environ.get("ENDEAVOR_API_KEY")
    if not (base and key):
        return None
    from openai import OpenAI
    return OpenAI(base_url=base, api_key=key, timeout=8.0)


def explain(verdict: dict, client=None) -> dict:
    client = client if client is not None else _default_client()
    if client is None:
        return {"text": template(verdict), "by": "template"}
    facts = {"decision": verdict["decision"], "cited_signals": verdict.get("cites", []),
             "decided_by": verdict.get("decided_by")}
    if verdict.get("confidence_gated"):
        facts["note"] = "an automated model was not confident enough to approve on its own"
    try:
        resp = client.responses.create(
            model=os.environ.get("ENDEAVOR_MODEL", DEFAULT_MODEL),
            instructions=("You explain card-payment risk decisions to a merchant's support team. "
                          "Write ONE plain sentence (max 30 words) saying what happened and why, "
                          "using only the signals given. No numbers you were not given."),
            input=str(facts),
            max_output_tokens=80,
        )
        text = " ".join(resp.output_text.split())[:300]
    except Exception as e:
        return {"text": template(verdict), "by": "template", "error": type(e).__name__}
    if not text or find_leaks(text):  # belt and braces: never display anything card-like
        return {"text": template(verdict), "by": "template", "error": "rejected_output"}
    return {"text": text, "by": os.environ.get("ENDEAVOR_MODEL", DEFAULT_MODEL)}
