"""Merchant-by-merchant training of the live specialists, and what each way of training is worth.

The five ProductCD merchants act as five nodes. Every specialist is trained three ways, and rows never leave
their merchant:

  local         each merchant trains alone on its own rows and scores its own transactions
  federated     FedAvg: every round each merchant trains a few Adam epochs from the global weights on its own
                rows, the server averages the weights by row count. Only weights move.
  personalised  the federated weights, then a few local epochs on the merchant's own rows (what the live node uses)

The stacker (the coordinator) is logistic regression on the four low/medium/high bands, with one set of band
cutoffs shared by every merchant, trained on a later slice of the training period than the specialists were.
For reference the shipped original 9-feature model (fl_weights.json) is scored on the same test rows.

    python -m cardguard.specialists.experiment
"""
from __future__ import annotations

import json

import numpy as np

from cardguard import ROOT
from cardguard.data import ieee_cis
from cardguard.training import fl
from cardguard.specialists.features import FAMILIES
from cardguard.specialists.model import (auc, band_cuts, fit_logistic, predict, recall_at_top, to_bands)

MODES = ("local", "federated", "personalised")
FIT_SHARE = 0.70                        # first 70% of the training period fits the specialists, the rest the stacker
ROUNDS, LOCAL_EPOCHS = 50, 6            # 300 gradient steps per client
LR_START, LR_END = 0.05, 0.005          # Adam restarts every round, so the step size decays to damp jitter
FINETUNE_EPOCHS, FINETUNE_LR = 40, 0.01


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float:
    return float("nan") if y.sum() == 0 or y.sum() == len(y) else auc(y, p)


def _split(fam: dict):
    """(fit, stack, test) row masks. The test period (last 20%) never fits anything."""
    dt, is_test = fam["dt"], fam["is_test"]
    train = ~is_test
    cut = np.quantile(dt[train], FIT_SHARE)
    return train & (dt <= cut), train & (dt > cut), is_test


def fedavg_weights(results: list[tuple[np.ndarray, int]]) -> np.ndarray:
    """Row-count-weighted mean of the clients' weight vectors."""
    total = sum(n for _, n in results)
    return sum(w * (n / total) for w, n in results)


def fedavg_train(parts: list[tuple[np.ndarray, np.ndarray]], dim: int,
                 rounds: int = ROUNDS, local_epochs: int = LOCAL_EPOCHS) -> np.ndarray:
    """FedAvg over clients' (X, y). Each client sees only its own pair; only weights are exchanged."""
    w = np.zeros(dim + 1)
    for r in range(rounds):
        lr = LR_START + (LR_END - LR_START) * r / max(rounds - 1, 1)
        w = fedavg_weights([(fit_logistic(X, y, epochs=local_epochs, lr=lr, init=w), len(y)) for X, y in parts])
    return w


def _metrics(y, q, test, vert, merchants) -> dict:
    return {"auc": auc(y[test], q[test]), "recall5": recall_at_top(y[test], q[test]),
            "per_merchant": {nm: _safe_auc(y[test & (vert == c)], q[test & (vert == c)]) for c, nm in merchants}}


def original_model(fam: dict) -> dict | None:
    """The shipped original 9-feature model (fl_weights.json, FedAvg over the same training rows) scored on the same
    test rows; None if the weights or the 9-feature cache are missing or the rows do not line up (a --limit run)."""
    path = ROOT / "fl_weights.json"
    if not ieee_cis.available() or not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    d = ieee_cis.load()
    y, vert = fam["y"], fam["vert"]
    merchants = [(c, nm) for c, nm in enumerate(fam["vert_names"]) if (vert == c).any()]
    if (saved["features"] != fl.FEATURES or len(d["y"]) != len(y) or not np.array_equal(d["is_test"], fam["is_test"])
            or any(not np.array_equal(d["vert"] == nm, vert == c) for c, nm in merchants)):
        return None
    _, _, test = _split(fam)
    return _metrics(y, fl.predict_proba(np.array(saved["weights"]), d["X"]), test, vert, merchants)


def run_federated(fam: dict, epochs: int = 300, rounds: int = ROUNDS, local_epochs: int = LOCAL_EPOCHS,
                  with_original: bool = True) -> dict:
    y, vert = fam["y"], fam["vert"]
    fit, stack, test = _split(fam)
    merchants = [(c, nm) for c, nm in enumerate(fam["vert_names"]) if (vert == c).any()]
    scores = {mode: {} for mode in MODES}
    spec = {mode: {} for mode in MODES}

    for name in FAMILIES:
        X = fam["families"][name]["X"]
        parts = {c: (X[(vert == c) & fit], y[(vert == c) & fit]) for c, _ in merchants}
        w_f = fedavg_train(list(parts.values()), X.shape[1], rounds, local_epochs)
        p = {"federated": predict(w_f, X)}
        p["local"], p["personalised"] = p["federated"].copy(), p["federated"].copy()
        for c, _ in merchants:
            Xm, ym = parts[c]
            rows = vert == c
            p["local"][rows] = predict(fit_logistic(Xm, ym, epochs=epochs), X[rows])
            p["personalised"][rows] = predict(
                fit_logistic(Xm, ym, epochs=FINETUNE_EPOCHS, lr=FINETUNE_LR, init=w_f), X[rows])
        for mode in MODES:
            scores[mode][name] = p[mode]
            spec[mode][name] = auc(y[test], p[mode][test])

    def stacked(mode: str) -> dict:
        cols = []
        for p in scores[mode].values():
            b = to_bands(p, band_cuts(p[stack]))  # one set of cutoffs for every merchant
            cols += [(b == 1).astype(float), (b == 2).astype(float)]
        Z = np.column_stack(cols)
        return _metrics(y, predict(fit_logistic(Z[stack], y[stack], epochs=epochs, lr=0.1), Z), test, vert, merchants)

    return {
        "rounds": rounds, "local_epochs": local_epochs,
        "merchants": {nm: {"fit_rows": int(((vert == c) & fit).sum()), "test_rows": int(((vert == c) & test).sum()),
                           "test_fraud": int(y[test & (vert == c)].sum())} for c, nm in merchants},
        "stack": {mode: stacked(mode) for mode in MODES},
        "specialist_auc": spec,
        "original": original_model(fam) if with_original else None,
    }


def report(res: dict) -> str:
    names = list(res["merchants"])
    L = [f"FedAvg: {res['rounds']} rounds x {res['local_epochs']} local epochs; stacker on the four bands\n",
         "merchant (node)    fit rows   test rows   test fraud"]
    for nm, m in res["merchants"].items():
        L.append(f"  {nm:14s}{m['fit_rows']:10,d}{m['test_rows']:12,d}{m['test_fraud']:12,d}")
    L.append("\nAUC on the test period, by merchant (\"all\" pools every merchant's test rows)")
    L.append(f"{'training':30s}{'all':>8s}{'catch@5%':>10s}" + "".join(f"{n:>8s}" for n in names))
    rows = [("original 9-feature (shipped)", res["original"])] if res.get("original") else []
    rows += [(f"specialists, {m}", res["stack"][m]) for m in MODES]
    for label, r in rows:
        L.append(f"{label:30s}{r['auc']:8.3f}{r['recall5']:10.3f}" + "".join(f"{r['per_merchant'][n]:8.3f}" for n in names))
    L.append("\nEach specialist on its own, overall AUC")
    L.append(f"{'specialist':16s}" + "".join(f"{m:>14s}" for m in MODES))
    for fam_name in FAMILIES:
        L.append(f"{fam_name:16s}" + "".join(f"{res['specialist_auc'][m][fam_name]:14.3f}" for m in MODES))
    return "\n".join(L)
