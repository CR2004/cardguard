"""Human decisions retrain the model; the lesson spreads without sharing rows; a node can join."""
import numpy as np

from cardguard.training import fl
from cardguard.training import retrain as rt
from cardguard.payment_processing import merchant


def _registry_small():
    r = rt.Registry(); r.nodes = {"node-electronics": "electronics", "node-travel": "travel", "node-digital": "digital"}
    return r


def test_two_human_labels_change_the_band_for_that_pattern():
    reg = _registry_small()
    w = fl.train_federated(rounds=30)[0]
    pattern = [0, 1, 0, 0, 0.2, 1, 1, 0.1, 0.05]   # micro amount, first purchase, at night, brand-new card
    out = rt.retrain(reg, "node-electronics", [(pattern, 1), (pattern, 1)], w)
    assert out["nodes"] == 3 and out["labels"] == 2
    assert out["after"][0] == "high"
    assert len(out["weights"]) == len(fl.FEATURES) + 1 and not np.allclose(out["weights"], w)


def test_retrain_without_labels_is_a_plain_federated_round():
    reg = _registry_small()
    w = fl.train_federated(rounds=5)[0]
    out = rt.retrain(reg, "node-electronics", [], w)
    assert out["before"] == [] and out["after"] == [] and len(out["weights"]) == len(w)


def test_join_adds_a_node_to_the_next_round():
    reg = _registry_small()
    assert reg.join("store-d", "travel")["nodes"] == 4
    try:
        reg.join("bad", "not-a-source"); assert False
    except ValueError:
        pass


def test_merchant_labels_from_review_and_chargeback(monkeypatch):
    from tests.test_merchant import MASTER_DE, buy
    from tests.stripe_fake import processor
    monkeypatch.setattr(merchant, "processor", processor())
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token"); H = {"Authorization": "Bearer rev-token"}
    merchant.ledger = merchant.MerchantLedger(); merchant.pending.clear(); merchant.seen.clear()
    merchant.labels.clear(); merchant.payments.clear(); merchant.registry = None; merchant._checkout_calls.clear()
    c = merchant.app.test_client()
    ok = buy(c)                                              # approved: appears in /payments
    code = ok["payment"]["auth_code"]
    assert c.get("/payments").get_json()[0]["auth_code"] == code
    assert c.post(f"/payments/{code}/dispute").status_code == 401                # reviewer only
    assert c.post(f"/payments/{code}/dispute", headers=H).get_json()["labels"] == 1   # chargeback -> fraud label
    assert c.post(f"/payments/{code}/dispute", headers=H).status_code == 404
    rid = buy(c, card=MASTER_DE, amount=90000)["review_id"]
    assert c.post(f"/reviews/{rid}/decline", headers=H).get_json()["labels"] == 2   # human decline -> fraud label
    assert all(len(f) == len(fl.FEATURES) and lab == 1 for f, lab in merchant.labels)
    assert "tok_" not in str(merchant.labels) and "5555" not in str(merchant.labels)   # features only
    monkeypatch.setattr(merchant, "registry", _registry_small())
    monkeypatch.setattr(merchant, "VERTICAL", "electronics")
    monkeypatch.setattr(merchant, "FL_WEIGHTS", merchant.FL_WEIGHTS.copy())   # restored after the test
    before = merchant.FL_WEIGHTS.copy()
    assert c.post("/agent/retrain").status_code == 401
    out = c.post("/agent/retrain", headers=H).get_json()
    assert out["nodes"] == 3 and out["labels"] == 2 and len(out["after"]) == 2   # node-electronics is in the registry
    assert not np.allclose(merchant.FL_WEIGHTS, before)
    assert c.post("/agent/join", json={"name": "store-d", "source": "travel"}, headers=H).get_json()["nodes"] == 4
    assert c.get("/agent/nodes").get_json()["count"] == 4
    assert c.post("/agent/retrain", headers=H, environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403


def test_join_cli(monkeypatch):
    from cardguard.training import join
    import json, urllib.request
    class R:
        def __init__(self): self.data = json.dumps({"joined": "store-d", "nodes": 4}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self.data
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=10, context=None: R())
    assert join.main(["--name", "store-d", "--source", "H"]) == 0
