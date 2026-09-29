"""Round 2: one targeted follow-up, to the bank only, when the store and the bank disagree.

Stripe is the payment rail; the bank only attests. It answers travel_check from private synthetic
history (a city and a time) that never leaves it; the answer can put a person in the loop but never
declines a payment on its own."""
import json
from types import SimpleNamespace as NS

import pytest

from cardguard.agentapp import agent_app as aa
from cardguard.bank import node as bank_node
from cardguard.bank.client import BankClient, sign
from cardguard.bank.node import CITIES, BankAttestor, History, Refused
from cardguard.decision import coordinator as coord
from cardguard.decision.guard import WireViolation, strip_for_wire
from cardguard.payment_processing import merchant
from tests import test_merchant
from tests.bank_fake import LocalBank
from tests.test_agentapp import CoordinatorGrid, FakeContext, FakeEvents
from tests.test_coordinator import LOW, FakeJev
from tests.test_merchant import MASTER_DE, buy

client = test_merchant.client  # the merchant node fixture (faked Stripe SDK, in-process bank), shared

ALL_CITIES = [c for cities in CITIES.values() for c in cities]
PRIVATE = ALL_CITIES + ["hours_ago", "declines_30d", "last_seen"]  # the bank's history, by name
MISMATCH = {**LOW, "country_mismatch": "yes", "issuer_behavior": "low", "issuer_recent_declines": "none"}
REF = "tok_abcdefghijklmnop"
ROUND_ONE = {"token": REF, "country_mismatch": "yes", "cvc_check": "pass", "issuer_behavior": "low"}  # calls for round 2


# ---------------------------------------------------------------- vocabulary and policy

def test_bank_facts_are_a_closed_vocabulary():
    for key, values in {"travel_check": ("plausible", "implausible", "unknown"),
                        "issuer_behavior": ("low", "medium", "high", "unknown"),
                        "issuer_recent_declines": ("none", "some", "many", "unknown")}.items():
        for v in values:
            assert strip_for_wire({key: v}) == {key: v}
        for bad in ("Berlin", "last seen 2h ago", "LOW"):
            with pytest.raises(WireViolation):
                strip_for_wire({key: bad})


def test_round_two_is_asked_only_when_store_and_bank_disagree():
    assert coord.needs_travel_check(MISMATCH)
    assert not coord.needs_travel_check({**MISMATCH, "country_mismatch": "no"})     # no disagreement
    assert not coord.needs_travel_check({**MISMATCH, "issuer_behavior": "medium"})   # the bank is not vouching
    assert coord.needs_travel_check({**MISMATCH, "issuer_behavior": "unknown"})      # a configured bank did not answer
    no_bank = {k: v for k, v in MISMATCH.items() if not k.startswith("issuer_")}
    assert not coord.needs_travel_check(no_bank)                                     # no bank configured at all
    assert not coord.needs_travel_check({**MISMATCH, "cvc_check": "unavailable"})
    assert not coord.needs_travel_check({**MISMATCH, "cvc_check": "fail"})           # already a hard decline
    assert not coord.needs_travel_check({**MISMATCH, "travel_check": "plausible"})   # asked once
    assert not coord.needs_travel_check({**MISMATCH, "amount_band": "high", "velocity_band": "high",
                                         "network_velocity_band": "high"})           # rules already decline


def test_implausible_travel_adds_a_person_and_never_declines():
    assert coord.rules(MISMATCH)["decision"] == "approve"
    floored = coord.rules({**MISMATCH, "travel_check": "implausible"})
    assert floored["decision"] == "step_up" and "travel_check=implausible" in floored["cites"]
    assert floored["score"] == coord.rules(MISMATCH)["score"]                      # no risk points added
    assert coord.rules({**MISMATCH, "travel_check": "plausible"})["decision"] == "approve"
    assert coord.rules({**MISMATCH, "travel_check": "unknown"})["decision"] == "approve"
    risky = {**MISMATCH, "amount_band": "high", "new_customer": "yes"}
    for travel in ("plausible", "implausible", "unknown"):
        assert coord.rules({**risky, "travel_check": travel})["decision"] == "step_up"  # never pushed to decline
    assert "travel_check=implausible" in coord.ALLOWED_CITES


def test_the_model_never_sees_round_two_and_cannot_turn_it_into_a_decline():
    facts = {**MISMATCH, "travel_check": "implausible"}
    confident = FakeJev(action="approve", confidence=0.99)
    out = coord.decide(facts, client=confident)
    assert out["decision"] == "step_up" and "travel_check" not in json.dumps(confident.seen_state)
    assert "issuer_behavior" in confident.seen_state["transaction_facts"]           # round-1 bands it may see
    assert coord.decide({**MISMATCH, "travel_check": "plausible"}, client=FakeJev(action="approve"))["decision"] == "approve"


# ---------------------------------------------------------------- the bank attestation node

def test_bank_answers_from_private_history_as_bands_only():
    bank = BankAttestor()
    assert bank.attest(REF, "North America") == {"issuer_behavior": "low", "issuer_recent_declines": "none"}
    assert bank.travel_check(REF, "North America", "Europe", "d1") == {"travel_check": "implausible"}  # in person hours ago
    assert bank.travel_check(REF, "North America", "North America", "d2") == {"travel_check": "plausible"}
    assert bank.travel_check(REF, "North America", "Europe", "d3", merchant_id="m2") == {"travel_check": "implausible"}
    elsewhere = BankAttestor()
    assert elsewhere.travel_check(REF, "North America", "Elsewhere", "d1") == {"travel_check": "implausible"}  # unmapped is still away
    with pytest.raises(Refused, match="already answered"):
        bank.travel_check(REF, "North America", "Europe", "d1")                    # no probing within a decision
    for bad_ref, region in (("4242424242424242", "Europe"), (REF, "Berlin")):
        with pytest.raises(Refused):
            bank.attest(bad_ref, region)
    with pytest.raises(Refused, match="does not match"):
        bank.attest(REF, "Europe")                                                 # a card's region is fixed at first sight
    now = 100 * 3600.0
    seeded = BankAttestor(history={REF: History("Lisbon", "Europe", now - 3600 * bank_node.MIN_TRAVEL_HOURS, 4)},
                          clock=lambda: now)
    assert seeded.attest(REF, "Europe") == {"issuer_behavior": "high", "issuer_recent_declines": "many"}
    assert seeded.travel_check(REF, "Europe", "North America", "d1") == {"travel_check": "plausible"}  # exactly the limit


def test_the_bank_history_ages_with_the_clock():
    t = [0.0]
    bank = BankAttestor(clock=lambda: t[0])
    assert bank.travel_check(REF, "North America", "Europe", "d1") == {"travel_check": "implausible"}
    t[0] += 3600 * bank_node.MIN_TRAVEL_HOURS
    assert bank.travel_check(REF, "North America", "Europe", "d2") == {"travel_check": "plausible"}


def test_one_answer_per_merchant_and_decision_and_no_sweeping_the_map():
    bank = BankAttestor()
    assert bank.travel_check(REF, "North America", "Europe", "d1", merchant_id="m1")
    assert bank.travel_check(REF, "North America", "Europe", "d1", merchant_id="m2")  # another merchant's decision d1
    bank.travel_check(REF, "North America", "Africa", "d2", merchant_id="m1")
    with pytest.raises(Refused, match="too many regions"):
        bank.travel_check(REF, "North America", "Latin America", "d3", merchant_id="m1")  # a third region per card
    assert bank.travel_check(REF, "North America", "Europe", "d4", merchant_id="m1")  # an already-asked region is fine
    with pytest.raises(Refused, match="unknown region"):
        bank.travel_check(REF, "North America", "Mars", "d5", merchant_id="m1")


def test_bank_bounds_travel_questions_per_card():
    bank = BankAttestor()
    for i in range(bank_node.TRAVEL_PER_CARD_PER_HOUR):
        bank.travel_check(REF, "Europe", "Europe", f"d{i}")
    with pytest.raises(Refused, match="too many"):
        bank.travel_check(REF, "Europe", "Europe", "d-extra")


def test_bank_routes_are_signed_one_shot_and_answer_bands(monkeypatch):
    monkeypatch.setattr(bank_node, "bank", BankAttestor())
    monkeypatch.setattr(bank_node, "gate", bank_node.Gate({"m1": "s1"}))
    c = bank_node.app.test_client()
    body = json.dumps({"card_ref": REF, "card_region": "North America", "buyer_region": "Europe", "decision_id": "d1"}).encode()
    post = lambda path, data, headers=None: c.post(path, data=data, content_type="application/json", headers=headers or {})
    assert post("/travel-check", body).status_code == 401                                        # unsigned
    assert post("/travel-check", body, sign("wrong", "m1", "POST", "/travel-check", body)).status_code == 401
    other = json.dumps({"card_ref": REF, "card_region": "North America", "buyer_region": "North America",
                        "decision_id": "d1"}).encode()
    assert post("/travel-check", other, sign("s1", "m1", "POST", "/travel-check", body)).status_code == 401  # body not signed
    signed = sign("s1", "m1", "POST", "/travel-check", body)
    ok = post("/travel-check", body, signed)
    assert ok.status_code == 200 and ok.get_json() == {"travel_check": "implausible"}
    assert post("/travel-check", body, signed).status_code == 401                                # replayed nonce
    assert post("/travel-check", body, sign("s1", "m1", "POST", "/travel-check", body)).status_code == 409  # same decision
    att = json.dumps({"card_ref": REF, "card_region": "North America"}).encode()
    assert post("/attest", att, sign("s1", "m1", "POST", "/attest", att)).get_json() == \
        {"issuer_behavior": "low", "issuer_recent_declines": "none"}
    missing = json.dumps({"card_ref": REF}).encode()
    assert post("/attest", missing, sign("s1", "m1", "POST", "/attest", missing)).status_code == 400


def test_bank_client_degrades_to_no_answer_when_the_bank_is_down():
    down = BankClient("http://127.0.0.1:9", "m1", "s1", timeout=0.5)
    assert down.attest(REF, "Europe") is None and down.travel_check(REF, "Europe", "Europe", "d1") is None


# ---------------------------------------------------------------- the merchant node, in-process

def test_in_process_round_two_reaches_the_bank_and_only_the_band_crosses(client):
    buy(client)                                                   # the card is now a returning customer here
    res = buy(client, country="DE", trace_id="abcdefghijklmnop")
    assert res["outcome"] == "needs_review" and "travel_check=implausible" in res["verdict"]["cites"]
    assert not res["verdict"].get("hard")
    travel = [e for e in merchant.ledger.entries if e["purpose"] == "travel-check"]
    assert [e["fields"] for e in travel] == [{"travel_check": "implausible"}]
    trace = client.get("/trace/abcdefghijklmnop").get_json()
    kinds = [e["kind"] for e in trace["events"]]
    assert kinds.index("processor.verify.reply") < kinds.index("bank.attest.reply") < kinds.index("coord.conflict") \
        < kinds.index("bank.travel.request") < kinds.index("bank.travel.reply") < kinds.index("gate.decision")
    gate = next(e for e in trace["events"] if e["kind"] == "gate.decision")
    assert gate["round_2"] and any(line["rule"] == "round_2" for line in gate["lines"])
    everything = json.dumps([trace, client.get("/ledger").get_json(), res])
    assert not any(p in everything for p in PRIVATE)


def test_no_round_two_without_disagreement(client):
    buy(client, trace_id="bcdefghijklmnopa")
    assert not [e for e in merchant.ledger.entries if e["purpose"] == "travel-check"]
    kinds = [e["kind"] for e in client.get("/trace/bcdefghijklmnopa").get_json()["events"]]
    assert "coord.conflict" not in kinds and "bank.travel.request" not in kinds and "bank.attest.reply" in kinds


def test_without_a_configured_bank_there_are_no_bank_facts_and_no_round_two(client, monkeypatch):
    monkeypatch.setattr(merchant, "bank", None)
    buy(client)
    res = buy(client, country="DE", trace_id="efghijklmnopabcd")
    fields = [e for e in merchant.ledger.entries if e["purpose"] == "fraud-risk"][-1]["fields"]
    assert "issuer_behavior" not in fields and "issuer_recent_declines" not in fields
    assert not [e for e in merchant.ledger.entries if e["purpose"] == "travel-check"]
    assert "travel_check=implausible" not in res["verdict"]["cites"]
    kinds = [e["kind"] for e in client.get("/trace/efghijklmnopabcd").get_json()["events"]]
    assert "bank.attest.request" not in kinds


def test_a_configured_bank_that_is_down_holds_the_disagreement_for_a_person(client, monkeypatch):
    down = LocalBank()
    down.attest = lambda *a: None
    down.travel_check = lambda *a: None
    monkeypatch.setattr(merchant, "bank", down)
    buy(client)                                                    # returning customer: rules alone would approve
    res = buy(client, country="DE")
    fields = [e for e in merchant.ledger.entries if e["purpose"] == "fraud-risk"][-1]["fields"]
    assert fields["issuer_behavior"] == "unknown"
    assert res["outcome"] == "needs_review" and res["verdict"]["round_2_unanswered"]


def test_a_bank_that_stops_answering_before_round_two_holds_for_a_person(client, monkeypatch):
    bank = LocalBank()
    bank.travel_check = lambda *a: None                            # attests in round 1, silent in round 2
    monkeypatch.setattr(merchant, "bank", bank)
    buy(client)
    res = buy(client, country="DE")
    assert res["outcome"] == "needs_review" and res["verdict"]["round_2_unanswered"]


def test_the_stripe_fingerprint_never_leaves_the_stripe_adapter(client):
    bank = LocalBank()
    merchant.bank = bank
    buy(client, trace_id="cdefghijklmnopab")
    buy(client, country="DE")
    seen = json.dumps([merchant.ledger.entries, bank.calls, client.get("/trace/cdefghijklmnopab").get_json(),
                       client.get("/ledger").get_json(), client.get("/reviews").get_json()])
    assert "fp_visa" not in seen and "pm_visa" not in json.dumps(merchant.ledger.entries)
    assert all(call[1].startswith("tok_") for call in bank.calls)  # the bank knows only the pseudonym


# ---------------------------------------------------------------- over the Grid

@pytest.mark.parametrize("answer", [
    {"facts": {"travel_check": "maybe"}},                          # off vocabulary
    {"error": "the bank did not answer"},                          # the node could not ask
    ["not", "an", "object"],                                       # not a fact set at all
    {"facts": {"travel_check": "plausible", "amount_band": "low"}},  # more than the band
])
def test_a_round_two_without_a_verified_band_holds_for_a_person(answer):
    grid = CoordinatorGrid(lambda body: {"facts": {**MISMATCH}} if body["purpose"] == "fraud-risk" else answer)
    verdict = aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": "d1"}))
    assert verdict["decision"] == "step_up" and verdict["round_2_unanswered"] and "travel_check" not in verdict["facts"]


def test_round_one_events_come_before_the_conflict():
    grid = CoordinatorGrid(lambda body: {"facts": {**MISMATCH}} if body["purpose"] == "fraud-risk" else
                           {"facts": {"travel_check": "implausible"}})
    events = FakeEvents()
    aa.coordinator_role(NS(prompt="decide", grid=grid, events=events), FakeContext(**{"agent.decision-id": "d1"}))
    steps = [e for e in events.items if e.get("type") == "cardguard.step"]
    assert [s["step"] for s in steps][:5] == ["nodes", "question", "reply", "network", "conflict"]
    assert any(s["step"] == "reply" and s.get("round") == 2 and s["status"] == "verified" for s in steps)


def test_a_round_one_fact_set_cannot_carry_the_round_two_answer():
    grid = CoordinatorGrid(lambda body: {"facts": {**MISMATCH, "travel_check": "plausible"}})
    verdict = aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": "d1"}))
    assert verdict["decided_by"] == "no_facts" and verdict["decision"] == "step_up"


def test_no_disagreement_means_exactly_one_question_on_the_grid():
    grid = CoordinatorGrid(lambda body: {"facts": {**LOW}})
    aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": "d1"}))
    assert [k for k, _ in grid.wire] == ["question", "reply"]


class TwoNodeGrid(CoordinatorGrid):
    """Two SuperNodes; only 101 has this decision's facts. Records where every question went."""

    def __init__(self, merchant_http):
        super().__init__(merchant_http, nodes=("101", "202"))
        self.sent = []

    def call(self, tc):
        if tc["name"] == "push_messages":
            args = json.loads(tc["arguments"])
            self.sent += [(m["dst_node_id"], json.loads(m["payload"])["purpose"]) for m in args["messages"]]
        out = super().call(tc)
        for mid, (dst, payload) in list(self.replies.items()):
            if dst == "202":
                self.replies[mid] = (dst, json.dumps({"error": "unknown decision"}))
        return out


def test_round_two_goes_only_to_the_node_that_answered_round_one():
    grid = TwoNodeGrid(lambda body: {"facts": {**MISMATCH}} if body["purpose"] == "fraud-risk"
                       else {"facts": {"travel_check": "implausible"}})
    verdict = aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": "d1"}))
    assert sorted(grid.sent) == [("101", "fraud-risk"), ("101", "travel-check"), ("202", "fraud-risk")]
    assert verdict["facts"]["travel_check"] == "implausible" and verdict["decision"] == "step_up"


def test_round_two_over_the_grid_reaches_the_bank_through_the_answering_node(client, monkeypatch):
    monkeypatch.setattr(merchant, "FEDERATION", "local-agent")
    grids = []

    def fake_flwr_run(decision_id):
        grid = CoordinatorGrid(lambda body: client.post("/agent/facts", json={**body, "node_id": "101"}).get_json())
        grids.append(grid)
        return aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": decision_id}))
    monkeypatch.setattr(merchant, "run_grid_decision", fake_flwr_run)
    res = buy(client, country="DE")
    assert res["verdict"]["decided_via"] == "flower:local-agent"
    wire = grids[0].wire
    assert [json.loads(p).get("purpose") for k, p in wire if k == "question"] == ["fraud-risk", "travel-check"]
    assert json.loads(wire[-1][1])["facts"] == {"travel_check": "implausible"}
    assert res["verdict"]["facts"]["travel_check"] == "implausible" and res["outcome"] == "needs_review"
    assert not any(p in json.dumps([wire, res]) for p in PRIVATE)


def test_round_two_needs_round_one_from_the_same_node(client):
    merchant.pending_decisions["d7"] = {"payload": {**ROUND_ONE}, "note": {}, "facts": None,
                                        "card_region": "North America", "buyer_region": "Europe"}
    ask = lambda node: client.post("/agent/facts", json={"decision_id": "d7", "purpose": "travel-check",
                                                         "node_id": node}).get_json()
    assert ask("101")["error"] == "round 2 follows round 1 from the same node"   # before round 1
    client.post("/agent/facts", json={"decision_id": "d7", "purpose": "fraud-risk", "node_id": "101"})
    assert ask("202")["error"] == "round 2 follows round 1 from the same node"   # another node
    merchant.pending_decisions.clear()


def test_a_refused_replay_from_another_node_does_not_rebind_the_decision(client):
    bank = LocalBank()
    merchant.bank = bank
    merchant.pending_decisions["d8"] = {"payload": {**ROUND_ONE}, "note": {}, "facts": None,
                                        "card_region": "North America", "buyer_region": "Europe"}
    try:
        ask = lambda purpose, node: client.post("/agent/facts", json={"decision_id": "d8", "purpose": purpose,
                                                                      "node_id": node}).get_json()
        assert "facts" in ask("fraud-risk", "101")
        assert ask("fraud-risk", "202")["error"] == "already disclosed for this decision"
        assert merchant.pending_decisions["d8"]["node_id"] == "101"
        assert ask("travel-check", "202")["error"] == "round 2 follows round 1 from the same node"
        assert ask("travel-check", "101")["facts"] == {"travel_check": "implausible"}
        assert "error" in ask("travel-check", "101")                                  # one answer per decision
        assert len([c for c in bank.calls if c[0] == "travel_check"]) == 2 and bank.calls[-1][-1] == "d8"
    finally:
        merchant.pending_decisions.clear()


def test_a_fallback_after_grid_round_two_reuses_the_banks_answer(client, monkeypatch):
    """The Flower run asked the bank, then no verdict came back: the store must not auto-approve."""
    monkeypatch.setattr(merchant, "FEDERATION", "local-agent")
    buy(client)                                                    # returning customer: round 1 alone would approve

    def flower_run_that_loses_its_verdict(decision_id):
        grid = CoordinatorGrid(lambda body: client.post("/agent/facts", json={**body, "node_id": "101"}).get_json())
        aa.coordinator_role(NS(prompt="decide", grid=grid, events=FakeEvents()), FakeContext(**{"agent.decision-id": decision_id}))
        return None                                                # e.g. the launcher timed out after round 2
    monkeypatch.setattr(merchant, "run_grid_decision", flower_run_that_loses_its_verdict)
    res = buy(client, country="DE")
    assert res["verdict"]["decided_via"] == "in-process"
    assert res["outcome"] == "needs_review" and "travel_check=implausible" in res["verdict"]["cites"]


def test_a_trace_id_is_never_reused_by_another_checkout(client):
    first = buy(client, trace_id="defghijklmnopabc")
    assert first["outcome"] == "approved"
    before = client.get("/trace/defghijklmnopabc").get_json()["events"]
    buy(client, trace_id="defghijklmnopabc")                       # same id: the second checkout is not traced
    assert client.get("/trace/defghijklmnopabc").get_json()["events"] == before
    assert all("trace_id" not in r for r in client.get("/reviews").get_json())


def test_a_risky_round_two_purchase_keeps_the_ledger_clean(client):
    buy(client, card=MASTER_DE, amount=90000)                      # DE card, US buyer: round 2 in-process
    seen = json.dumps(merchant.ledger.entries)
    assert "pm_de" not in seen and "fp_de" not in seen and "ver_" not in seen
    assert not any(p in seen for p in PRIVATE)


def test_the_merchant_node_refuses_a_round_two_that_is_not_called_for(client):
    merchant.pending_decisions["d6"] = {"payload": {"token": REF, "country_mismatch": "no", "cvc_check": "pass"},
                                        "note": {}, "facts": None, "card_region": "North America", "buyer_region": "Europe"}
    try:
        client.post("/agent/facts", json={"decision_id": "d6", "purpose": "fraud-risk", "node_id": "101"})
        out = client.post("/agent/facts", json={"decision_id": "d6", "purpose": "travel-check", "node_id": "101"}).get_json()
        assert out["error"] == "round 2 is not called for"
        assert not [e for e in merchant.ledger.entries if e["purpose"] == "travel-check"]
    finally:
        merchant.pending_decisions.clear()
