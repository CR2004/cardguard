"""Real-data checks. Skipped unless datasets/train_transaction.csv (or its feature cache) exists."""
import pytest

from cardguard.training import fl
from cardguard.data import ieee_cis as fl_data

pytestmark = pytest.mark.skipif(not fl_data.available(), reason="IEEE-CIS data not present")


@pytest.fixture(scope="module")
def data():
    return fl_data.load()


def test_features_are_bounded_and_match_interface(data):
    X = data["X"]
    assert X.shape[1] == len(fl.FEATURES) == len(fl_data.FEATURES)
    assert X.min() >= 0 and X.max() <= 1
    assert set(data["vert"]) == set(fl_data.VERTICALS)


def test_amount_cuts_are_per_vertical(data):
    cuts = data["cuts"]
    assert cuts["W"][2] > cuts["C"][2]  # W is a bigger-ticket vertical than C
    for v in fl_data.VERTICALS:
        assert cuts[v][0] < cuts[v][1] < cuts[v][2]


def test_federated_matches_pooled_and_beats_every_single_vertical(data):
    r = fl.report_real(rounds=30)
    fed, pooled = r["federated"]["all"], r["centralized_upper_bound"]["all"]
    assert fed > pooled - 0.02
    assert all(fed > r[f"local_only_{v}"]["all"] + 0.03 for v in fl_data.VERTICALS)
