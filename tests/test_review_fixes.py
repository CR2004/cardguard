"""Regression tests for the code-review findings."""

import numpy as np
import pytest

from cardguard.decision import llm
from cardguard.payment_processing import merchant
from tests.test_merchant import MASTER_DE, buy
from tests.stripe_fake import processor



def test_processor_outage_is_a_refusal_not_a_500(monkeypatch):
    from tests.stripe_fake import processor
    monkeypatch.setattr(merchant, "processor", processor(broken=True))
    merchant.ledger = merchant.MerchantLedger(); merchant.seen.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    res = buy(c)
    assert res["outcome"] == "processor_rejected" and res["charged"] is False
    assert merchant.processor.audit[-1]["status"] == "rejected"


def test_review_rejects_unknown_actions(monkeypatch):
    monkeypatch.setattr(merchant, "processor", processor())
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token"); H = {"Authorization": "Bearer rev-token"}
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear(); merchant.labels.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    rid = buy(c, card=MASTER_DE, amount=90000)["review_id"]
    assert c.post(f"/reviews/{rid}/aprove", headers=H).status_code == 400 and merchant.labels == []
    assert c.post(f"/reviews/{rid}/approve", headers=H).get_json()["outcome"] == "approved_by_human"


def test_token_rate_limit_after_verify_voids_and_blocks(monkeypatch):
    node = processor()
    monkeypatch.setattr(merchant, "processor", node)
    merchant.ledger = merchant.MerchantLedger(); merchant.seen.clear(); merchant._checkout_calls.clear()
    merchant.ledger.MAX_PER_TOKEN_PER_HOUR = 1
    c = merchant.app.test_client()
    assert buy(c)["outcome"] == "approved"
    res = buy(c)
    assert res["outcome"] == "blocked" and "rate limit" in res["blocked"][0] and node.audit[-1]["event"] == "void"


def test_retrain_is_idempotent_from_the_shipped_weights(monkeypatch):
    from tests.test_retrain import _registry_small
    monkeypatch.setattr(merchant, "processor", processor())
    monkeypatch.setattr(merchant, "registry", _registry_small())
    monkeypatch.setattr(merchant, "VERTICAL", "W")            # not a synthetic node: it still joins the round
    monkeypatch.setattr(merchant, "FL_WEIGHTS", merchant.FL_WEIGHTS.copy())
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token"); H = {"Authorization": "Bearer rev-token"}
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear()
    merchant.labels.clear(); merchant.payments.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    code = buy(c)["payment"]["auth_code"]; c.post(f"/payments/{code}/dispute", headers=H)
    first = c.post("/agent/retrain", headers=H).get_json(); w1 = merchant.FL_WEIGHTS.copy()
    c.post("/agent/retrain", headers=H); w2 = merchant.FL_WEIGHTS.copy()
    assert first["nodes"] == 4 and np.allclose(w1, w2)        # same labels -> same weights, no compounding


def test_dispute_evidence_uses_the_nodes_own_processor_log(monkeypatch):
    node = processor()
    monkeypatch.setattr(merchant, "processor", node)
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token"); H = {"Authorization": "Bearer rev-token"}
    merchant.ledger = merchant.MerchantLedger(); merchant.seen.clear(); merchant.payments.clear(); merchant.labels.clear(); merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    code = buy(c)["payment"]["auth_code"]; c.post(f"/payments/{code}/dispute", headers=H)
    ev = c.get(f"/payments/{code}/evidence", headers=H).get_json()["evidence"]
    assert [e["event"] for e in ev["processor_events"]] == ["verify", "authorize"]
    assert ev["facts_at_decision"]["network_velocity_band"] == "low" and ev["checks"]["ledger_chain_intact"]


def test_llm_endpoint_and_key_are_chosen_as_a_pair(monkeypatch):
    for v in ("LLM_BASE_URL", "LLM_API_KEY", "FLWR_RUNTIME_BASE_URL", "FLWR_RUNTIME_API_KEY", "ENDEAVOR_BASE_URL", "ENDEAVOR_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://third.party/v1")
    monkeypatch.setenv("FLWR_RUNTIME_API_KEY", "flower-secret")
    monkeypatch.setenv("FLWR_RUNTIME_BASE_URL", "http://127.0.0.1:8010/v1/runtime")
    assert llm.endpoint() == ("http://127.0.0.1:8010/v1/runtime", "flower-secret")   # never Flower's key to a third party


def test_seed_cuts_come_from_the_real_quantiles_when_available():
    from cardguard.data import ieee_cis
    if ieee_cis.available():
        assert merchant.SEED_CUTS["W"][1] == round(ieee_cis.load()["cuts"]["W"][1])
    assert set(merchant.SEED_CUTS) >= {"W", "C", "R", "H", "S"}


def test_network_identity_is_the_node_id_not_the_declared_store():
    """A compromised node cannot fabricate sightings for another merchant: identity = node id + store."""
    from types import SimpleNamespace as NS
    from flwr.app import RecordDict
    from cardguard.agentapp import agent_app as aa
    from tests.test_agentapp import CoordinatorGrid, FakeContext, FakeEvents
    from tests.test_coordinator import LOW
    state = RecordDict()
    for i, claimed in enumerate(["store-a", "store-b", "store-c"]):   # one node claims three stores
        grid = CoordinatorGrid(lambda body, s=claimed: {"facts": LOW, "merchant_id": s}, nodes=("7",))
        v = aa.coordinator_role(NS(prompt="x", grid=grid, events=FakeEvents()), FakeContext(state=state, **{"agent.decision-id": f"d{i}"}))
    assert v["network"] == {"band": "low", "merchants": ["7"]}   # production: three claims from one node are one merchant
    assert v["merchant_id"] == "7:store-c"                          # the claim is recorded, not trusted


def test_conflicting_replies_from_two_nodes_go_to_a_human():
    from cardguard.agentapp import agent_app as aa
    from cardguard.decision.coordinator import Verifier
    from tests.test_coordinator import LOW
    import json
    replies = [{"src_node_id": n, "payload": json.dumps({"decision_id": "d1", "facts": LOW, "merchant_id": "s"}), "error": None}
               for n in ("7", "8")]
    v = aa.decide_from_replies(replies, "d1", Verifier())
    assert v["decided_by"] == "no_facts" and any("conflicting" in r["reason"] for r in v["rejected"])


def _demo_review_node(monkeypatch):
    monkeypatch.setattr(merchant, "processor", processor())
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "demo-review-credential")
    monkeypatch.setattr(merchant, "DEMO_CONTROLS", True)
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear(); merchant.labels.clear(); merchant._checkout_calls.clear()
    return merchant.app.test_client()


DEMO_H = {merchant.DEMO_REVIEWER_HEADER: "1"}


def test_demo_page_gets_a_session_cookie_never_the_reviewer_token(monkeypatch):
    """Demo only: this node's own page on this machine gets an HttpOnly, SameSite=Strict session, derived from the
    token but never the token: not in the body, not in the cookie. Nobody else gets one."""
    c = _demo_review_node(monkeypatch)
    res = c.get("/config")
    assert b"demo-review-credential" not in res.data and "reviewer_token" not in res.get_json()
    assert all("demo-review-credential" not in h for h in res.headers.getlist("Set-Cookie"))
    cookie = c.get_cookie(merchant.DEMO_REVIEWER_COOKIE)
    assert cookie.http_only and cookie.same_site == "Strict" and cookie.value != "demo-review-credential"
    remote = merchant.app.test_client().get("/config", environ_base={"REMOTE_ADDR": "10.0.0.7"})
    assert not remote.headers.getlist("Set-Cookie")
    monkeypatch.setattr(merchant, "ALLOWED_HOSTS", merchant.ALLOWED_HOSTS | {"demo.tunnel.example"})
    tunnel = merchant.app.test_client().get("/config", base_url="http://demo.tunnel.example")  # relayed from 127.0.0.1
    assert tunnel.status_code == 200 and not tunnel.headers.getlist("Set-Cookie")
    monkeypatch.setattr(merchant, "DEMO_CONTROLS", False)
    assert not merchant.app.test_client().get("/config").headers.getlist("Set-Cookie")
    monkeypatch.setattr(merchant, "DEMO_CONTROLS", True); monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "")
    assert not merchant.app.test_client().get("/config").headers.getlist("Set-Cookie")


@pytest.mark.parametrize("page", ["http://127.0.0.1:4242", "http://localhost:4242", "http://localhost"])
def test_demo_session_approves_and_declines_through_the_reviewer_gate(monkeypatch, page):
    """The page as run_demo.py serves it (127.0.0.1:4242), and the other loopback names the node answers to."""
    c = _demo_review_node(monkeypatch)
    assert c.get("/config", base_url=page).headers.getlist("Set-Cookie")
    approve, decline = (buy(c, card=MASTER_DE, amount=90000)["review_id"] for _ in range(2))
    res = c.post(f"/reviews/{approve}/approve", headers=DEMO_H, base_url=page)
    assert res.get_json()["outcome"] == "approved_by_human"
    assert c.post(f"/reviews/{decline}/decline", headers=DEMO_H, base_url=page).get_json()["outcome"] == "declined_by_human"
    assert merchant.pending == {}


def test_demo_session_fails_safely_and_the_payment_stays_held(monkeypatch):
    """No bypass: without the session, without the header, from another machine, with demo controls off, or with
    no reviewer credential configured, the gate refuses and nothing is charged or labelled."""
    c = _demo_review_node(monkeypatch)
    rid = buy(c, card=MASTER_DE, amount=90000)["review_id"]
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H).status_code == 401            # no session cookie
    c.set_cookie(merchant.DEMO_REVIEWER_COOKIE, "forged")
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H).status_code == 401            # a forged session
    c.get("/config")
    assert c.post(f"/reviews/{rid}/approve").status_code == 401                            # no header
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H,
                  environ_base={"REMOTE_ADDR": "10.0.0.7"}).status_code == 401             # another machine
    monkeypatch.setattr(merchant, "ALLOWED_HOSTS", merchant.ALLOWED_HOSTS | {"demo.tunnel.example"})
    tunnel = merchant.app.test_client()                                                    # a tunnel, valid session
    tunnel.set_cookie(merchant.DEMO_REVIEWER_COOKIE, merchant._demo_reviewer_session(), domain="demo.tunnel.example")
    assert tunnel.post(f"/reviews/{rid}/approve", headers=DEMO_H, base_url="http://demo.tunnel.example").status_code == 401
    monkeypatch.setattr(merchant, "ALLOWED_HOSTS", merchant.ALLOWED_HOSTS | {"localhost.evil.example"})
    lookalike = merchant.app.test_client()                                                 # a loopback look-alike name
    assert not lookalike.get("/config", base_url="http://localhost.evil.example").headers.getlist("Set-Cookie")
    lookalike.set_cookie(merchant.DEMO_REVIEWER_COOKIE, merchant._demo_reviewer_session(), domain="localhost.evil.example")
    assert lookalike.post(f"/reviews/{rid}/approve", headers=DEMO_H, base_url="http://localhost.evil.example").status_code == 401
    for relayed in ({"X-Forwarded-For": "203.0.113.9"}, {"Forwarded": "for=203.0.113.9"}):  # a relay that says so
        assert not merchant.app.test_client().get("/config", headers=relayed).headers.getlist("Set-Cookie")
        assert c.post(f"/reviews/{rid}/approve", headers={**DEMO_H, **relayed}).status_code == 401
    monkeypatch.setattr(merchant, "DEMO_CONTROLS", False)
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H).status_code == 401            # demo controls off
    monkeypatch.setattr(merchant, "DEMO_CONTROLS", True); monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "")
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H).status_code == 401            # no credential at all
    assert rid in merchant.pending and merchant.labels == []
    assert merchant.processor.audit[-1]["event"] == "verify"                               # nothing charged


def test_a_wrong_bearer_or_a_rotated_credential_is_refused(monkeypatch):
    """The session is bound to the configured credential: rotating REVIEWER_TOKEN revokes it."""
    c = _demo_review_node(monkeypatch)
    rid = buy(c, card=MASTER_DE, amount=90000)["review_id"]
    assert c.post(f"/reviews/{rid}/approve", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    c.get("/config")                                                                       # minted under the old one
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rotated-review-credential")
    assert c.post(f"/reviews/{rid}/approve", headers=DEMO_H).status_code == 401
    assert rid in merchant.pending
