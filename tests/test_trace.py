"""The live investigation trace is display only, and it must never carry card data either."""
import json

from cardguard.payment_processing import trace as tr
from tests import test_merchant
from tests.test_merchant import buy

client = test_merchant.client  # the merchant node fixture, shared


def test_card_like_values_are_dropped_and_references_masked():
    t = tr.Trace("abcdefghijklmnop")
    e = t.add("store.facts", evidence={"token": "tok_abcdefghijklmnop", "amount_band": "low"})
    assert e["evidence"] == {"token": "tok_ab…nop", "amount_band": "low"}
    bad = t.add("guard.blocked", reason="leaked 4242 4242 4242 4242 exp 12/30")
    assert bad["kind"] == "redacted" and "4242" not in json.dumps(bad) and "12/30" not in json.dumps(bad)
    ids = t.add("coord.nodes", nodes=["123", "456"], run_id="not-a-run")
    assert ids["nodes"] == ["123", "456"] and "run_id" not in ids      # ids are shape-checked, never free text
    assert [x["seq"] for x in t.since(0)] == [1, 2]


def test_trace_ids_are_minted_letters_only_and_the_store_is_bounded():
    store = tr.TraceStore()
    assert store.open(None) is None and store.open("1234567890123456") is None and store.open("../../etc") is None
    letters = "abcdefghijklmnop"
    for i in range(tr.MAX_TRACES + 5):
        store.open("".join(letters[(i >> (4 * k)) & 15] for k in range(4)) + "a" * 12)
    assert len(store.traces) == tr.MAX_TRACES


def test_trace_endpoint_follows_one_checkout(client, monkeypatch):
    from cardguard.payment_processing import merchant
    monkeypatch.setattr(merchant, "traces", tr.TraceStore())  # module-level store: start empty
    assert client.get("/trace/abcdefghijklmnop").get_json() == {"events": [], "done": False, "known": False}
    buy(client, trace_id="abcdefghijklmnop")
    out = client.get("/trace/abcdefghijklmnop").get_json()
    kinds = [e["kind"] for e in out["events"]]
    assert out["done"] and kinds[0] == "payment.started" and kinds[-1] == "outcome"
    assert "gate.decision" in kinds and out["card_numbers_seen_by_coordinator"] == 0
    assert "4242" not in json.dumps(out)
    assert [e["seq"] for e in client.get("/trace/abcdefghijklmnop?after=2").get_json()["events"]][0] == 3


def test_ui_is_served_from_the_merchant_origin(client, tmp_path, monkeypatch):
    """The investigation UI is built into web/dist and served by the merchant node at its own origin."""
    from cardguard.payment_processing import merchant
    monkeypatch.setattr(merchant, "WEB_DIST", tmp_path)
    missing = client.get("/")
    assert missing.status_code == 503 and b"pnpm" in missing.data
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>CardGuard</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    page = client.get("/")
    assert page.status_code == 200 and page.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/../index.html").status_code == 404
    vocab = client.get("/config").get_json()["wire_vocabulary"]
    assert vocab["travel_check"] == ["implausible", "plausible", "unknown"] and "token" not in vocab
