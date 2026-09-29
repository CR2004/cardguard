"""The network agent's fact: the same card at several merchants within minutes."""
import pytest

from cardguard.decision import network as net
from cardguard.decision.coordinator import decide, rules
from tests.test_coordinator import LOW


def test_bands_by_distinct_merchants_in_window():
    w = net.NetworkWatch()
    assert w.observe("tok_a", "store-a", 0.0) == ("low", ["store-a"])
    assert w.observe("tok_a", "store-a", 10.0)[0] == "low"          # same store again: still one merchant
    assert w.observe("tok_a", "store-b", 20.0) == ("medium", ["store-a", "store-b"])
    assert w.observe("tok_a", "store-c", 30.0)[0] == "high"
    assert w.observe("tok_b", "store-c", 31.0)[0] == "low"          # a different card is unaffected
    assert w.observe("tok_a", "store-d", 30.0 + net.WINDOW + 1)[0] == "low"   # the window expired


def test_round_trip_and_bad_ids():
    w = net.NetworkWatch(); w.observe("tok_a", "store-a", 1.0)
    assert net.NetworkWatch.from_json(w.to_json()).rows == w.rows
    assert net.NetworkWatch.from_json("garbage").rows == []
    with pytest.raises(ValueError):
        w.observe("tok_a", "Bad Store!", 2.0)


def test_network_fact_moves_the_decision():
    assert rules({**LOW, "new_customer": "yes"})["decision"] == "approve"
    v = decide({**LOW, "new_customer": "yes", "network_velocity_band": "high"})
    assert v["decision"] in {"step_up", "decline"} and "network_velocity_band=high" in v["cites"]
    v = decide({**LOW, "new_customer": "yes", "amount_band": "medium", "model_risk_band": "medium", "network_velocity_band": "high"})
    assert v["decision"] == "decline"


def test_context_persistence_round_trip():
    from flwr.app import Context, RecordDict
    ctx = Context(run_id=1, node_id=1, node_config={}, state=RecordDict(), run_config={})
    w = net.load_from_context(ctx); assert w.rows == []
    w.observe("tok_a", "store-a", 5.0); net.save_to_context(ctx, w)
    assert net.load_from_context(ctx).rows == [["tok_a", "store-a", 5.0]]
