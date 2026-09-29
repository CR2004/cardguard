"""The specialists' data, features and merchant-by-merchant training (offline: synthetic data, no CSV needed)."""
import numpy as np
import pytest

from cardguard.specialists import data as sdata
from cardguard.specialists import federated
from cardguard.specialists.features import FAMILIES, build_families, history_features
from cardguard.specialists.live import FEATURES
from cardguard.specialists.model import BAND_NAMES, band_cuts, to_bands


@pytest.fixture(scope="module")
def raw():
    return sdata.synthetic_raw(n=8000, seed=1)


@pytest.fixture(scope="module")
def fam(raw):
    return build_families(raw)


def test_history_by_hand():
    raw = {"dt": np.array([0., 100, 200, 300, 200000, 400]),
           "amt": np.full(6, 50.0), "prod": np.zeros(6, dtype=int),
           "card": np.array([[1, 1, 1, 1]] * 5 + [[2, 1, 1, 1]], dtype=float),
           "addr1": np.full(6, 100.0), "pemail": np.zeros(6, dtype=int)}
    h = history_features(raw)
    # time order: rows 0-3 (card 1, within 24h; velocity counts PRIOR rows), row 5 (card 2, t=400),
    # then row 4 (card 1 again, 2 days later: the earlier ones have aged out of the 24h window)
    assert h["velocity"].tolist() == [0.0, 0.1, 0.2, 0.3, 0.0, 0.0]
    assert h["prior_count"].tolist() == [0, 1, 2, 3, 4, 0]


def test_history_has_no_lookahead(raw):
    """A row's history features must not change when later rows are removed."""
    k = 3000
    order = np.argsort(raw["dt"], kind="stable")
    prefix = order[:k]
    sub = {key: (v[prefix] if isinstance(v, np.ndarray) and len(v) == len(order) else v) for key, v in raw.items()}
    full, part = history_features(raw), history_features(sub)
    for name in full:
        assert np.array_equal(full[name][prefix], part[name]), name


def test_test_period_rows_never_shape_training_features(raw):
    """Cutoffs and caps come from training rows only: distorting test-period rows leaves train rows alone."""
    a = build_families(raw)
    test = a["is_test"]
    bent = dict(raw)
    bent["amt"] = np.where(test, raw["amt"] * 1000 + 7, raw["amt"])
    bent["addr2"] = np.where(test, 12.0, raw["addr2"])
    b = build_families(bent)
    assert np.array_equal(a["is_test"], b["is_test"])
    for name in FAMILIES:
        assert np.array_equal(a["families"][name]["X"][~test], b["families"][name]["X"][~test]), name


def test_families_are_exactly_what_the_live_node_computes(fam):
    assert FAMILIES == list(FEATURES)
    for name, f in fam["families"].items():
        assert f["names"] == FEATURES[name] and f["X"].shape[1] == len(FEATURES[name])
        assert 0 <= f["X"].min() and f["X"].max() <= 1, name
    assert fam["caps"]["prior_count"] >= 1


def test_bands_use_the_closed_vocabulary():
    s = np.random.default_rng(0).random(1000)
    b = to_bands(s, band_cuts(s))
    assert set(b.tolist()) == {0, 1, 2}
    assert {BAND_NAMES[i] for i in b.tolist()} == {"low", "medium", "high"}
    assert 0.03 < (b == 2).mean() < 0.07 and 0.13 < (b == 1).mean() < 0.17


def test_fedavg_weights_is_the_row_weighted_mean():
    w = federated.fedavg_weights([(np.array([0.0, 4.0]), 1), (np.array([4.0, 0.0]), 3)])
    assert np.allclose(w, [3.0, 1.0])


def test_each_federated_client_trains_only_on_its_own_rows(monkeypatch):
    """The server side only ever sees weight vectors: fit_logistic is called with one client's rows at a time."""
    rng = np.random.default_rng(0)
    parts = [(rng.random((n, 3)), (rng.random(n) < 0.1).astype(float)) for n in (40, 70, 55)]
    seen = []
    real = federated.fit_logistic

    def spy(X, y, **kw):
        seen.append(len(y))
        return real(X, y, **kw)

    monkeypatch.setattr(federated, "fit_logistic", spy)
    w = federated.fedavg_train(parts, dim=3, rounds=2, local_epochs=2)
    assert set(seen) == {40, 70, 55} and len(seen) == 6  # never a pooled call, 3 clients x 2 rounds
    assert w.shape == (4,) and np.isfinite(w).all()


def test_merchant_by_merchant_measurement_runs_and_stacking_beats_any_one_specialist(fam):
    r = federated.run_federated(fam, epochs=150, rounds=25, local_epochs=4, with_original=False)
    assert set(r["stack"]) == set(federated.MODES) and set(r["merchants"]) == set(fam["vert_names"])
    for mode in federated.MODES:
        assert r["stack"][mode]["auc"] > max(r["specialist_auc"][mode].values()) + 0.02  # the families complement
    assert r["stack"]["federated"]["auc"] > r["stack"]["local"]["auc"]  # merchants gain from federating
    assert "personalised" in federated.report(r)
