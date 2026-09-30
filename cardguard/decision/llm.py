"""One place for the OpenAI-compatible client every model-facing module uses.

Inside a Flower AgentApp the runtime injects FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY.
Elsewhere: ENDEAVOR_BASE_URL / ENDEAVOR_API_KEY, or a generic LLM_BASE_URL / LLM_API_KEY.
No endpoint configured -> None, and every caller falls back to a template. Never retries: a
model that is down must never hold up a payment.
"""
from __future__ import annotations

import os

RETRIES = 2  # extra attempts on a 5xx before the template answers (Flower's Endeavor provider 502s often)
DEFAULT_MODEL = "flwrlabs/endeavor-1.0"  # Flower Endeavor, as served through the Flower runtime; override with ENDEAVOR_MODEL / LLM_MODEL


PAIRS = (("LLM_BASE_URL", "LLM_API_KEY"), ("FLWR_RUNTIME_BASE_URL", "FLWR_RUNTIME_API_KEY"),
         ("ENDEAVOR_BASE_URL", "ENDEAVOR_API_KEY"))


def endpoint() -> tuple[str, str] | None:
    """A base URL and the key that belongs to IT: a key is never sent to another provider's URL."""
    for base_var, key_var in PAIRS:
        base, key = os.environ.get(base_var), os.environ.get(key_var)
        if base and key:
            return base, key
    return None


def via() -> str:
    """'flower' when the configured endpoint is Flower's (the AgentApp runtime or ENDEAVOR_*), else 'custom'."""
    for base_var, key_var in PAIRS:
        if os.environ.get(base_var) and os.environ.get(key_var):
            return "custom" if base_var == "LLM_BASE_URL" else "flower"
    return "custom"


def client(timeout: float = 8.0):
    pair = endpoint()
    if pair is None:
        return None
    from openai import OpenAI
    return OpenAI(base_url=pair[0], api_key=pair[1], timeout=timeout, max_retries=0)


def model_name() -> str:
    return (os.environ.get("LLM_MODEL") or os.environ.get("ENDEAVOR_MODEL") or DEFAULT_MODEL).strip()


def ask(instructions: str, input_text: str, fallback: str, client=None, timeout: float = 8.0,
        max_output_tokens: int = 80, max_chars: int = 300) -> dict:
    """One model call for display text: tidy whitespace, cap length, refuse anything card-like,
    and fall back to the given template on any failure. Returns {'text', 'by', ['error']}."""
    from cardguard.decision.guard import find_leaks
    client = client if client is not None else globals()["client"](timeout=timeout)
    if client is None:
        return {"text": fallback, "by": "template"}
    text, error = "", None
    for attempt in range(RETRIES + 1):
        try:
            resp = client.responses.create(model=model_name(), instructions=instructions, input=input_text,
                                           max_output_tokens=max_output_tokens)
            text = " ".join(resp.output_text.split())[:max_chars]
            break
        except Exception as e:  # noqa: BLE001 - a model that is down never blocks anything
            error = type(e).__name__
            # Flower's Endeavor provider answers 502 intermittently (seen live Sep 29): one more try, then the template.
            if attempt == RETRIES or getattr(e, "status_code", 0) < 500:
                return {"text": fallback, "by": "template", "error": error}
    if not text or find_leaks(text, cvv_words=False):
        return {"text": fallback, "by": "template", "error": "rejected_output"}
    return {"text": text, "by": model_name(), "via": via()}
