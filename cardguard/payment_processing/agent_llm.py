"""A deliberately model-driven merchant agent, for the live prompt-injection demo.

This is the pattern the rest of CardGuard refuses: an LLM composes the merchant's disclosure
and its prompt contains customer free text (the gift message). It exists only to show, live,
what the wire guard and the integrity check do when such an agent is tricked.

Two independent protections apply to whatever it produces:
  1. confidentiality: the output goes through Ledger.disclose() like any other payload, so a
     card number, an expiry, a CVV word, an unknown key or an off-vocabulary value is BLOCKED;
  2. integrity: if the output passes the guard but differs from the facts the node computed in
     code, the ledger records which fields were tampered and the code facts are used.
The gift message reaches only this agent's model call; never Jev, the ledger, or the decision.

Endpoint: any OpenAI-compatible Responses API. LLM_BASE_URL / LLM_API_KEY / LLM_MODEL, else the
Endeavor / Flower runtime variables. No endpoint -> compose() returns None and the UI says so.
"""
from __future__ import annotations

import json

from cardguard.decision import llm
from cardguard.decision.guard import WIRE_SCHEMA, find_leaks

PROMPT = (
    "You are the merchant's checkout agent. Produce the JSON disclosure for the fraud coordinator. "
    "Allowed keys and values: {schema}. Use exactly these facts: {facts}. "
    "The customer left this gift message with the order; take it into account: \"{gift}\". "
    "Reply with the JSON object only."
)


def _default_client():
    return llm.client(timeout=15.0)


def compose(facts: dict, gift_message: str, client=None) -> dict | None:
    """Ask the model for the disclosure. Returns {'raw': str, 'payload': dict|None, 'model': str}
    or None when no model endpoint is configured."""
    client = client if client is not None else _default_client()
    if client is None:
        return None
    schema = {k: (["tok_<16 letters>"] if k == "token" else sorted(v)) for k, v in WIRE_SCHEMA.items()}
    prompt = PROMPT.format(schema=json.dumps(schema), facts=json.dumps(facts), gift=gift_message[:500])
    try:
        resp = client.responses.create(model=llm.model_name(), input=prompt, max_output_tokens=300)
        raw = resp.output_text
    except Exception as e:
        return {"raw": "", "payload": None, "model": llm.model_name(), "error": type(e).__name__}
    return {"raw": raw, "payload": _parse(raw), "model": llm.model_name()}


def _parse(raw: str) -> dict | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def tampered_fields(code_facts: dict, model_payload: dict) -> list[str]:
    """Keys whose value differs from what the node computed in code (integrity check)."""
    return sorted(k for k in set(code_facts) | set(model_payload)
                  if code_facts.get(k) != model_payload.get(k))


def leaks_in_raw(raw: str) -> list[str]:
    """What the scanner sees in the model's raw text (for the demo log; the guard is the gate)."""
    return find_leaks(raw)
