"""Dispute evidence agent: when a chargeback arrives, assemble the evidence and draft the response.

Evidence comes only from what the merchant node already holds and from the issuer's audit: the
banded facts disclosed at decision time, the verdict and who decided, the verification and
authorization events with their times, and the network view. No card data exists to include.
A model (Endeavor / any OpenAI-compatible endpoint; template fallback) drafts the merchant's
response for a HUMAN to approve; the draft is leak-scanned before anyone sees it.
"""
from __future__ import annotations


from cardguard.decision import audit, llm
from cardguard.decision.guard import find_leaks, json_values


def assemble_evidence(auth_code: str, payment: dict, ledger_entries: list[dict], issuer_log: list[dict]) -> dict:
    """Everything the merchant can legitimately say about this payment, and nothing else."""
    t = payment["t"]
    facts = dict(payment.get("facts") or {})  # the facts the decision actually used, kept with the payment
    events = [{"event": a["event"], "status": a["status"], "at": a["t"], **({"cvc_check": a["cvc_check"]} if "cvc_check" in a else {})}
              for a in issuer_log if abs(a["t"] - t) < 120 and a["event"] in {"verify", "authorize"}]
    evidence = {
        "auth_code": auth_code, "amount_cents": payment["amount"], "store": payment.get("store"),
        "decided_at": t, "facts_at_decision": {k: v for k, v in facts.items() if k != "token"},
        "issuer_events": events,
        "checks": {
            "security_code": facts.get("cvc_check", "unknown"),
            "billing_country_matched_buyer": facts.get("country_mismatch") == "no",
            "network_velocity": facts.get("network_velocity_band", "unknown"),
            "ledger_chain_intact": audit.verify(ledger_entries)[0],
        },
    }
    if any(find_leaks(v) for v in json_values(evidence)):
        raise ValueError("evidence contains a card-like value")
    return evidence


def template(evidence: dict) -> str:
    c = evidence["checks"]
    return (f"We contest chargeback on authorization {evidence['auth_code']} for ${evidence['amount_cents'] / 100:.2f}. "
            f"At the time of purchase the card security code check was '{c['security_code']}', the billing country "
            f"{'matched' if c['billing_country_matched_buyer'] else 'did not match'} the buyer's location, and our "
            f"cross-merchant network saw this card at {c['network_velocity']} velocity. The issuer verified and "
            f"authorized the payment ({len(evidence['issuer_events'])} logged events), and our tamper-evident ledger is intact.")


def draft_response(evidence: dict, client=None) -> dict:
    """A draft for the human to approve. The model sees only the evidence dict (banded facts and events)."""
    return llm.ask(("Draft a short, factual chargeback response for a merchant, in at most four sentences, "
                    "using only the evidence given. Never invent details."),
                   str(evidence), fallback=template(evidence), client=client, timeout=15.0,
                   max_output_tokens=250, max_chars=1200)
