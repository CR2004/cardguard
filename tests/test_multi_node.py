"""One SuperNode per store: each store is its own merchant process with its own node. What must hold:
the same card gets the same letters-only reference at every node (CARD_REF_KEY), a node answers a
page cross-origin only for configured peers, each SuperNode talks to its own merchant, and run_demo
lays the nodes out without ever sharing a bank secret or the STORES setting."""
import os

from types import SimpleNamespace as NS

import run_demo
from cardguard.agentapp import agent_app
from cardguard.payment_processing import merchant
from cardguard.payment_processing.stripe_processor import StripeProcessor
from tests.stripe_fake import fake_sdk


def test_card_reference_is_shared_across_nodes_only_with_a_network_key(monkeypatch):
    monkeypatch.setenv("CARD_REF_KEY", "ab" * 32)
    a = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk())
    b = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk())
    assert a.card_ref("fp_1") == b.card_ref("fp_1") and a.card_ref("fp_1") != a.card_ref("fp_2")
    assert a.card_ref("fp_1").startswith("tok_") and a.card_ref("fp_1")[4:].isalpha()
    monkeypatch.delenv("CARD_REF_KEY")
    c = StripeProcessor("sk_test_x", "pk_test_x", sdk=fake_sdk())
    assert c.card_ref("fp_1") != a.card_ref("fp_1")  # without the key: per process only


def test_peers_are_parsed_and_off_vocabulary_ids_dropped():
    peers = merchant.parse_peers("store-a=http://127.0.0.1:4242/, store-b=http://127.0.0.1:4252,Bad Store=http://x,c=ftp://x")
    assert peers == [{"store": "store-a", "url": "http://127.0.0.1:4242"}, {"store": "store-b", "url": "http://127.0.0.1:4252"}]


def test_cross_origin_answers_only_peer_pages(monkeypatch):
    monkeypatch.setattr(merchant, "PEER_ORIGINS", {"http://127.0.0.1:4252", "http://localhost:4252"})
    monkeypatch.setattr(merchant, "PEERS", [{"store": "store-b", "url": "http://127.0.0.1:4252"}])
    c = merchant.app.test_client()
    pre = c.options("/checkout", headers={"Origin": "http://127.0.0.1:4252", "Host": "127.0.0.1:4242"})
    assert pre.status_code == 204 and pre.headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:4252"
    assert "Authorization" in pre.headers["Access-Control-Allow-Headers"]
    other = c.options("/checkout", headers={"Origin": "http://evil.example", "Host": "127.0.0.1:4242"})
    assert other.status_code == 403 and "Access-Control-Allow-Origin" not in other.headers
    cfg = c.get("/config", headers={"Origin": "http://evil.example", "Host": "127.0.0.1:4242"})
    assert "Access-Control-Allow-Origin" not in cfg.headers and cfg.get_json()["peers"][0]["store"] == "store-b"
    cfg = c.get("/config", headers={"Origin": "http://localhost:4252", "Host": "127.0.0.1:4242"})
    assert cfg.headers["Access-Control-Allow-Origin"] == "http://localhost:4252"


def test_each_supernode_asks_its_own_merchant(monkeypatch):
    run_cfg = {"agent.merchant-api": "http://127.0.0.1:4242"}
    monkeypatch.delenv("CARDGUARD_MERCHANT_API", raising=False)
    assert agent_app.merchant_api_for(NS(run_config=run_cfg, node_config={})) == "http://127.0.0.1:4242"
    monkeypatch.setenv("CARDGUARD_MERCHANT_API", "http://127.0.0.1:4252/")
    assert agent_app.merchant_api_for(NS(run_config=run_cfg, node_config={})) == "http://127.0.0.1:4252"
    assert agent_app.merchant_api_for(NS(run_config=run_cfg, node_config={"merchant-api": "http://127.0.0.1:4262"})) == "http://127.0.0.1:4262"
    monkeypatch.delenv("CARDGUARD_MERCHANT_API")
    assert agent_app.merchant_api_for(NS(run_config={}, node_config=None)) == agent_app.DEFAULT_MERCHANT_API


def test_run_demo_lays_out_one_node_per_store():
    plan = run_demo.node_plan(["store-a", "store-b", "store-c"])
    assert plan == [("store-a", 4242), ("store-b", 4252), ("store-c", 4262)]
    base = {"STORES": "store-a,store-b,store-c", "REVIEWER_TOKEN": "demo-review-2026", "STRIPE_SECRET_KEY": "sk_test_x"}
    env = run_demo.node_env(base, "store-b", 4252, plan, "ab" * 32, "secret-b")
    assert "STORES" not in env and env["MERCHANT_ID"] == "store-b" and env["MERCHANT_PORT"] == "4252"
    assert env["MERCHANT_PEERS"] == "store-a=http://127.0.0.1:4242,store-b=http://127.0.0.1:4252,store-c=http://127.0.0.1:4262"
    assert "127.0.0.1:4252" in env["MERCHANT_HOSTS"] and env["BANK_SECRET"] == "secret-b" and env["CARD_REF_KEY"] == "ab" * 32
    assert env["LABELS_FILE"].endswith(os.path.join(".demo", "labels_store-b.jsonl")) and env["REVIEWER_TOKEN"] == "demo-review-2026"
