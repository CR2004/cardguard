"""End-to-end flow tests in MOCK mode (no Stripe key, no network)."""
import os

os.environ.pop("STRIPE_SECRET_KEY", None)

import pytest

import merchant


@pytest.fixture
def client(monkeypatch):
    # What Stripe reports for its 4242 test card when a CVC is entered: a passed check.
    monkeypatch.setattr(merchant, "card_facts", lambda pm: {
        "country": "US", "funding": "credit", "fingerprint": pm, "cvc_check": "pass"})
    merchant.ledger.entries.clear()
    merchant.pending.clear()
    merchant.seen.clear()
    return merchant.app.test_client()


def buy(client, pm="pm_a", amount=2000, country="US", sabotage=False, hour=14):
    return client.post("/checkout", json={"payment_method_id": pm, "amount_cents": amount,
                                          "buyer_country": country, "sabotage": sabotage,
                                          "hour": hour}).get_json()


def test_normal_purchase_is_charged(client):
    res = buy(client)
    assert res["outcome"] == "approved"
    assert res["payment"]["status"] == "succeeded"


def test_risky_purchase_goes_to_human_and_decline_means_no_charge(client):
    res = buy(client, amount=90000, country="DE")
    assert res["outcome"] == "needs_review"
    rid = res["review_id"]
    assert client.get("/reviews").get_json()[0]["id"] == rid
    out = client.post(f"/reviews/{rid}/decline").get_json()
    assert out == {"outcome": "declined_by_human", "charged": False}


def test_human_approval_charges(client):
    rid = buy(client, amount=90000, country="DE")["review_id"]
    assert client.post(f"/reviews/{rid}/approve").get_json()["payment"]["status"] == "succeeded"


def test_sabotage_blocked_and_nothing_leaks(client):
    res = buy(client, sabotage=True)
    assert res["outcome"] == "blocked" and len(res["blocked"]) == 3 and res["charged"] is False
    led = client.get("/ledger").get_json()
    assert all(e["status"] == "BLOCKED" for e in led["entries"])
    assert led["card_numbers_seen_by_coordinator"] == 0


def test_ledger_never_contains_payment_method_id(client):
    buy(client, pm="pm_secret_123")
    assert "pm_secret_123" not in str(client.get("/ledger").get_json())


def test_federated_model_band_crosses_wire_as_band_only(client):
    buy(client, amount=90000, country="DE")
    fields = client.get("/ledger").get_json()["entries"][-1]["fields"]
    assert fields["model_risk_band"] == "high"


def test_refuses_live_keys(monkeypatch):
    import importlib
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
    with pytest.raises(SystemExit):
        importlib.reload(merchant)
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    importlib.reload(merchant)
