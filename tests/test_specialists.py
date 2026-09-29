"""Specialist experiment (offline: synthetic data; the real CSVs are not needed)."""
import numpy as np
import pytest

from cardguard.specialists import data as sdata
from cardguard.specialists import experiment
from cardguard.specialists.features import FAMILIES, build_families, history_features
from cardguard.specialists.model import BAND_NAMES, band_cuts, to_bands


@pytest.fixture(scope="module")
def raw():
    return sdata.synthetic_raw(n=8000, seed=1)


@pytest.fixture(scope="module")
def fam(raw):
    return build_families(raw)


def _slice(raw, k):
    return {key: (v[:k] if isinstance(v, np.ndarray) else v) for key, v in raw.items()}


def test_identity_flags_parse_known_formats():
    f = dict(zip(sdata.IDENT_NAMES, sdata.identity_flags({
        "DeviceType": "mobile", "DeviceInfo": "SM-G960F Build/R16NW", "id_30": "Android 7.0",
        "id_31": "samsung browser 6.2", "id_33": "1920x1080", "id_23": "IP_PROXY:ANONYMOUS",
        "id_15": "New", "id_29": "NotFound"})))
    assert f["mobile"] == f["dev_samsung"] == f["os_android"] == f["br_samsung"] == 1.0
    assert f["proxy_anonymous"] == f["id15_new"] == f["id29_notfound"] == 1.0
    assert f["desktop"] == f["dev_missing"] == f["os_missing"] == f["screen_missing"] == 0.0
    assert 0 < f["screen_area"] <= 1


def test_identity_flags_missing_row_is_all_missing_markers():
    f = dict(zip(sdata.IDENT_NAMES, sdata.identity_flags({})))
    assert f["dev_missing"] == f["os_missing"] == f["br_missing"] == f["screen_missing"] == 1.0
    assert f["mobile"] == f["desktop"] == f["proxy_anonymous"] == 0.0


def test_history_by_hand():
    raw = {"dt": np.array([0., 100, 200, 300, 200000, 400]),
           "amt": np.full(6, 50.0), "prod": np.zeros(6, dtype=int),
           "card": np.array([[1, 1, 1, 1]] * 5 + [[2, 1, 1, 1]], dtype=float),
           "addr1": np.full(6, 100.0), "pemail": np.zeros(6, dtype=int), "remail": np.full(6, -1)}
    h = history_features(raw)
    # time order: rows 0-3 (card 1, within 24h; velocity counts PRIOR rows), row 5 (card 2, t=400),
    # then row 4 (card 1 again, 2 days later: the earlier ones have aged out of the 24h window)
    assert h["velocity"].tolist() == [0.0, 0.1, 0.2, 0.3, 0.0, 0.0]
    # card 2 shares (addr1, email) with card 1, and row 4 is processed after card 2 appeared
    assert h["cards_per_pair"].tolist() == [0, 0, 0, 0, 1, 1]
    assert h["cards_per_email"].tolist() == [0, 0, 0, 0, 1, 1] and h["emails_per_card"].max() == 0
    assert h["loc_known"].tolist() == [0, 1, 1, 1, 1, 0] and h["loc_shift"].sum() == 0


def test_history_has_no_lookahead(raw):
    """A row's history features must not change when later rows are removed."""
    k = 3000
    order = np.argsort(raw["dt"], kind="stable")
    prefix = order[:k]
    sub = {key: (v[prefix] if isinstance(v, np.ndarray) and len(v) == len(order) else v)
           for key, v in raw.items()}
    full, part = history_features(raw), history_features(sub)
    for name in full:
        assert np.array_equal(full[name][prefix], part[name]), name


def test_test_period_rows_never_shape_training_features(raw):
    """Cuts, caps and rarity come from training rows only: distorting test-period rows leaves train rows alone."""
    a = build_families(raw)
    bent = dict(raw)
    test = a["is_test"]
    bent["amt"] = np.where(test, raw["amt"] * 1000 + 7, raw["amt"])
    bent["dist1"] = np.where(test, 1e6, raw["dist1"])
    bent["pemail"] = np.where(test, 3, raw["pemail"])
    b = build_families(bent)
    assert np.array_equal(a["is_test"], b["is_test"])
    for name in ("transaction", "identity", "geo"):
        assert np.array_equal(a["families"][name]["X"][~test], b["families"][name]["X"][~test]), name


def test_families_bounded_named_and_device_abstains(fam, raw):
    assert list(fam["families"]) == FAMILIES
    for name, f in fam["families"].items():
        assert f["X"].shape[1] == len(f["names"]) and len(set(f["names"])) == len(f["names"]), name
        assert 0 <= f["X"].min() and f["X"].max() <= 1, name
    dev = fam["families"]["device"]
    assert dev["covered"].tolist() == raw["has_identity"].tolist()
    assert not dev["X"][~dev["covered"]].any()  # no identity row -> no device evidence, not fake evidence
    assert 0.2 < dev["covered"].mean() < 0.4


def test_bands_use_the_closed_vocabulary():
    s = np.random.default_rng(0).random(1000)
    b = to_bands(s, band_cuts(s))
    assert set(b.tolist()) == {0, 1, 2}
    assert {BAND_NAMES[i] for i in b.tolist()} == {"low", "medium", "high"}
    assert 0.03 < (b == 2).mean() < 0.07 and 0.13 < (b == 1).mean() < 0.17


def test_stack_beats_every_single_specialist(fam, monkeypatch):
    monkeypatch.setenv("SPECIALISTS_SKIP_BASELINE", "1")
    r = experiment.run(fam, epochs=200)
    best = max(v["auc"] for v in r["single"].values())
    assert r["stack_scores"]["auc"] > best + 0.05
    assert r["stack_bands"]["auc"] > best + 0.05  # the version that only ships low/medium/high
    assert r["single"]["merchant"]["auc"] < 0.6  # a specialist with no signal stays uninformative
    assert r["single"]["device"]["coverage"] < 0.4
    assert set(r["ablation"]) == {f"without_{n}" for n in r["single"]}
    assert "STACK (scores)" in experiment.report(r)


def test_unknown_model_is_rejected():
    from cardguard.specialists.model import fit_model
    with pytest.raises(ValueError):
        fit_model("forest", np.zeros((10, 2)), np.zeros(10))


def test_lgbm_specialists_run_and_stack_still_wins(fam, monkeypatch):
    pytest.importorskip("lightgbm")
    monkeypatch.setenv("SPECIALISTS_SKIP_BASELINE", "1")
    r = experiment.run(fam, epochs=100, model="lgbm")
    assert r["model"] == "lgbm"
    assert r["stack_scores"]["auc"] > max(v["auc"] for v in r["single"].values()) + 0.03
