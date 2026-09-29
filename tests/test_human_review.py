"""Human-in-the-loop: second look on soft declines, structured opinions, audit, persisted labels, stats."""
import json

import pytest

from cardguard.decision import audit
from cardguard.payment_processing import merchant, review_store
from tests.bank_fake import LocalBank
from tests.stripe_fake import processor
from tests.test_merchant import CVC_FAIL, MASTER_DE, REVIEWER, VISA, buy

REAL_DECIDE = merchant.decide
SOFT = {"decision": "decline", "score": 9, "cites": ["amount_band=high", "country_mismatch=yes"], "decided_by": "rules"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(merchant, "processor", processor())
    monkeypatch.setattr(merchant, "bank", LocalBank())  # in-process, like test_merchant: no real connection attempts
    monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token")
    monkeypatch.setattr(merchant.label_store, "path", str(tmp_path / "labels.jsonl"))
    monkeypatch.setattr(merchant, "review_audit", review_store.ReviewAudit(str(tmp_path / "audit.jsonl")))
    merchant._checkout_calls.clear(); merchant.ledger = merchant.MerchantLedger()
    for d in (merchant.pending, merchant.seen, merchant.payments, merchant.card_first_seen, merchant.card_last_seen):
        d.clear()
    merchant.labels.clear()
    return merchant.app.test_client()


def soft(monkeypatch, c, **kw):
    monkeypatch.setattr(merchant, "decide", lambda facts, client=None: dict(SOFT))
    return buy(c, card=MASTER_DE, amount=90000, **kw)


def test_soft_decline_is_queued_not_charged_and_hard_decline_is_not(client, monkeypatch):
    res = soft(monkeypatch, client)
    assert res["outcome"] == "needs_review" and res["suspected"] == "soft_decline"
    assert merchant.processor.audit[-1]["event"] == "verify"          # verification still open, nothing charged
    assert client.get("/reviews").get_json()[0]["suspected"] == "soft_decline"
    monkeypatch.undo()
    monkeypatch.setattr(merchant, "processor", processor()); monkeypatch.setattr(merchant, "REVIEWER_TOKEN", "rev-token")
    monkeypatch.setattr(merchant, "bank", LocalBank())
    n = len(merchant.pending)
    hard = buy(client, card=CVC_FAIL)
    assert hard["outcome"] == "declined" and hard["verdict"].get("hard") and len(merchant.pending) == n


def test_second_look_switch_off_keeps_final_declines(client, monkeypatch):
    monkeypatch.setattr(merchant, "SECOND_LOOK_ON_DECLINE", False)
    res = soft(monkeypatch, client)
    assert res["outcome"] == "declined" and not merchant.pending


def test_human_can_overturn_or_confirm_a_soft_decline(client, monkeypatch):
    rid = soft(monkeypatch, client)["review_id"]
    out = client.post(f"/reviews/{rid}/approve", headers=REVIEWER, json={"reason": "customer_verified"}).get_json()
    assert out["outcome"] == "approved_by_human" and out["payment"]["status"] == "succeeded"
    assert merchant.labels[-1][1] == 0
    rid = soft(monkeypatch, client)["review_id"]
    out = client.post(f"/reviews/{rid}/decline", headers=REVIEWER).get_json()
    assert out["payment"]["status"] == "voided" and merchant.labels[-1][1] == 1


def test_unreviewed_soft_decline_is_voided_after_an_hour(client, monkeypatch):
    soft(monkeypatch, client)
    merchant._prune_state(merchant.time.time() + 3601)
    assert not merchant.pending and merchant.processor.audit[-1]["event"] == "void"
    assert merchant.review_audit.entries[-1]["outcome"] == "expired_voided" and merchant.review_audit.verify()


def test_opinion_validation_changes_nothing_on_bad_input(client, monkeypatch):
    rid = soft(monkeypatch, client)["review_id"]
    p = f"/reviews/{rid}/decline"
    assert client.post(p, json={"reason": "vibes"}).status_code == 401
    for body, hdr in [({"reason": "vibes"}, {}), ({"note": "x" * 201}, {}), ({"note": "card 4242 4242 4242 4242"}, {}),
                      ({"note": 5}, {}), ({}, {"X-Reviewer-Id": "bad id!"}), ({}, {"X-Reviewer-Id": "a" * 33})]:
        assert client.post(p, headers={**REVIEWER, **hdr}, json=body).status_code == 400, body
    assert client.post(p, headers=REVIEWER, data="not json", content_type="application/json").status_code == 400
    assert rid in merchant.pending and not merchant.labels and not merchant.review_audit.entries
    ok = client.post(p, headers={**REVIEWER, "X-Reviewer-Id": "ana-1"}, json={"reason": "card_testing", "note": "looks like testing"})
    assert ok.status_code == 200


def test_audit_records_opinion_without_note_or_card_data(client, monkeypatch):
    rid = soft(monkeypatch, client)["review_id"]
    note = "called the customer, seems fine"
    client.post(f"/reviews/{rid}/approve", headers={**REVIEWER, "X-Reviewer-Id": "ana"}, json={"reason": "known_customer", "note": note})
    e = merchant.review_audit.entries[-1]
    assert (e["review_id"], e["decision"], e["reason"], e["reviewer"], e["suspected"]) == (rid, "approve", "known_customer", "ana", "soft_decline")
    assert e["cites"] == SOFT["cites"] and e["model_band"] in {"low", "medium", "high"} and e["note_len"] == len(note)
    assert note not in json.dumps(e) and "pm_" not in json.dumps(e) and merchant.review_audit.verify()
    assert note not in json.dumps(merchant.ledger.entries)                        # never disclosed
    merchant.review_audit.entries[-1]["reason"] = "other"
    assert not merchant.review_audit.verify()                                     # tampering is detected


def test_labels_persist_across_restart_and_survive_corruption(client, monkeypatch, tmp_path):
    rid = soft(monkeypatch, client)["review_id"]
    client.post(f"/reviews/{rid}/decline", headers=REVIEWER, json={"reason": "ring_pattern"})
    monkeypatch.setattr(merchant, "decide", REAL_DECIDE)
    code = buy(client, card=VISA)["payment"]["auth_code"]
    client.post(f"/payments/{code}/dispute", headers=REVIEWER)
    path = merchant.label_store.path
    recs = [json.loads(line) for line in open(path)]
    assert [(r["source"], r["label"], r["reason"]) for r in recs] == [("review", 1, "ring_pattern"), ("chargeback", 1, None)]
    assert all(r["decision_id"] and len(r["features"]) == len(merchant.fl.FEATURES) and r["t"] for r in recs)
    text = open(path).read()
    assert "pm_" not in text and "tok_" not in text
    assert review_store.LabelStore(path, len(merchant.fl.FEATURES)).load() == merchant.labels
    with open(path, "a") as f:
        f.write("{torn\n" + json.dumps({"features": [1], "label": 1}) + "\n" + json.dumps({"features": [float("nan")] * 9, "label": 1}) + "\n")
    assert len(review_store.LabelStore(path, len(merchant.fl.FEATURES)).load()) == 2
    assert review_store.LabelStore(str(tmp_path / "missing.jsonl"), 9).load() == []


def test_label_file_is_capped(tmp_path):
    store = review_store.LabelStore(str(tmp_path / "l.jsonl"), 2)
    for i in range(2 * review_store.MAX_LABELS + 5):
        store.append([float(i), 0.0], i % 2, "review", None, "d", 0.0)
    loaded = store.load()
    assert len(loaded) <= review_store.MAX_LABELS and loaded[-1][0][0] == 2 * review_store.MAX_LABELS + 4


def test_review_stats_are_reviewer_only_and_correct(client, monkeypatch):
    assert client.get("/review-stats").status_code == 401
    ids = [soft(monkeypatch, client)["review_id"] for _ in range(3)]
    client.post(f"/reviews/{ids[0]}/approve", headers=REVIEWER, json={"reason": "customer_verified"})
    client.post(f"/reviews/{ids[1]}/decline", headers=REVIEWER, json={"reason": "card_testing"})
    s = client.get("/review-stats", headers=REVIEWER).get_json()
    assert s["reviews_done"] == 2 and s["queue_length"] == 1 and s["oldest_age_seconds"] is not None
    assert s["soft_decline_overturn_rate"] == 0.5 and s["by_reason"] == {"customer_verified": 1, "card_testing": 1}
    assert s["median_seconds_to_review"] is not None and s["audit_chain_ok"] is True
    assert s["reasons"] == list(review_store.REASONS)


def test_agreement_rate_uses_model_band():
    def e(band, decision):
        return {"kind": "opinion", "outcome": "declined" if decision == "decline" else "approved", "decision": decision,
                "model_band": band, "suspected": "step_up", "reason": None, "seconds_to_review": 1.0}
    entries = [e("high", "decline"), e("medium", "approve"), e("low", "approve"), e("low", "decline")]
    assert review_store.stats(entries, {}, 0)["model_human_agreement"] == 0.5


def test_two_reviewer_rule_needs_different_ids_and_only_for_approvals(client, monkeypatch):
    monkeypatch.setattr(merchant, "TWO_REVIEWER_ABOVE_CENTS", 50000)
    rid = soft(monkeypatch, client)["review_id"]
    a, b = {**REVIEWER, "X-Reviewer-Id": "ana"}, {**REVIEWER, "X-Reviewer-Id": "ben"}
    p = f"/reviews/{rid}/approve"
    assert client.post(p, headers=REVIEWER).status_code == 400                    # needs an id
    first = client.post(p, headers=a)
    assert first.status_code == 202 and first.get_json()["outcome"] == "awaiting_second"
    assert rid in merchant.pending and not merchant.labels and merchant.processor.audit[-1]["event"] == "verify"
    assert client.post(p, headers=a).status_code == 409                           # same person cannot complete it
    done = client.post(p, headers=b).get_json()
    assert done["outcome"] == "approved_by_human" and merchant.labels[-1][1] == 0
    assert [x["outcome"] for x in merchant.review_audit.entries] == ["awaiting_second", "approved"]
    assert merchant.review_audit.entries[-1]["first_reviewer"] == "ana"
    rid2 = soft(monkeypatch, client)["review_id"]                                 # a decline needs one reviewer
    assert client.post(f"/reviews/{rid2}/decline", headers=a).get_json()["outcome"] == "declined_by_human"
    monkeypatch.setattr(merchant, "TWO_REVIEWER_ABOVE_CENTS", 0)                  # off: one approval completes
    rid3 = soft(monkeypatch, client)["review_id"]
    assert client.post(f"/reviews/{rid3}/approve", headers=REVIEWER).get_json()["outcome"] == "approved_by_human"


def test_no_human_action_can_touch_a_hard_decline(client):
    res = buy(client, card=CVC_FAIL)
    assert res["outcome"] == "declined" and "review_id" not in res and not merchant.pending
    assert client.post("/reviews/deadbeef/approve", headers=REVIEWER).status_code == 404


def test_review_audit_chain_verifies_from_file(client, monkeypatch):
    rid = soft(monkeypatch, client)["review_id"]
    client.post(f"/reviews/{rid}/decline", headers=REVIEWER)
    path = merchant.review_audit.path
    reloaded = review_store.ReviewAudit(path)
    assert reloaded.entries == merchant.review_audit.entries and reloaded.verify() and audit.verify(reloaded.entries)[0]
    lines = open(path).read().splitlines()
    lines[0] = lines[0].replace('"decline"', '"approve"')
    open(path, "w").write("\n".join(lines) + "\n")
    bad = review_store.ReviewAudit(path)
    assert bad.entries == [] and bad.load_error
