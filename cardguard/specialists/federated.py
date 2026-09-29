"""Federated measurement: does merchant-level FedAvg cost or help once the specialists are split?

experiment.py trains every specialist on all merchants' rows pooled in one process, so it says nothing
about federated learning. Here each specialist is trained four ways across the five ProductCD
merchants (nodes), rows never leaving their merchant:

  central       all merchants' rows pooled (the earlier experiment; needs everyone's data in one place)
  local         each merchant trains alone on its own rows and scores its own transactions
  federated     FedAvg: every round each merchant trains a few Adam epochs from the global weights on
                its own rows, the server averages the weights by row count. Only weights move.
  personalised  federated weights, then a few local epochs on the merchant's own rows

Specialists are logistic regression here because FedAvg averages weight vectors. The stacker (the
coordinator) is trained pooled on the stack split for every mode, so the modes differ only in how the
specialists were trained. Same time-ordered splits and test set as experiment.py.

    python -m cardguard.specialists.experiment --federated [--deployable]
"""
from __future__ import annotations

import numpy as np

from cardguard.specialists.experiment import MIN_FRAUD, MIN_ROWS, _split, _stack_matrix
from cardguard.specialists.features import FAMILIES
from cardguard.specialists.model import auc, fit_logistic, predict, recall_at_top

MODES = ("central", "local", "federated", "personalised")
ROUNDS, LOCAL_EPOCHS = 50, 6            # 300 gradient steps per client: same budget as the central fit
LR_START, LR_END = 0.05, 0.005          # Adam restarts every round, so the step size decays to damp jitter
FINETUNE_EPOCHS, FINETUNE_LR = 40, 0.01


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float:
    return float("nan") if y.sum() == 0 or y.sum() == len(y) else auc(y, p)


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


def run_federated(fam: dict, epochs: int = 300, rounds: int = ROUNDS, local_epochs: int = LOCAL_EPOCHS) -> dict:
    y, vert = fam["y"], fam["vert"]
    fit, stack, test = _split(fam)
    merchants = [(code, name) for code, name in enumerate(fam["vert_names"]) if (vert == code).any()]
    scores = {mode: {} for mode in MODES}
    cov, spec = {}, {"central": {}, "federated": {}}

    for name in FAMILIES:
        if name not in fam["families"]:
            continue
        X, c = fam["families"][name]["X"], fam["families"][name]["covered"]
        if (fit & c).sum() < MIN_ROWS or y[fit & c].sum() < MIN_FRAUD:
            continue
        cov[name] = c
        parts = {}
        for code, _ in merchants:
            m = (vert == code) & fit & c
            if m.sum() > 0:
                parts[code] = (X[m], y[m])
        w_c = fit_logistic(X[fit & c], y[fit & c], epochs=epochs)
        w_f = fedavg_train(list(parts.values()), X.shape[1], rounds, local_epochs)
        p = {"central": predict(w_c, X), "federated": predict(w_f, X)}
        p["local"], p["personalised"] = p["federated"].copy(), p["federated"].copy()  # fallback: no rows here
        for code, (Xm, ym) in parts.items():
            rows = vert == code
            p["local"][rows] = predict(fit_logistic(Xm, ym, epochs=epochs), X[rows])
            p["personalised"][rows] = predict(
                fit_logistic(Xm, ym, epochs=FINETUNE_EPOCHS, lr=FINETUNE_LR, init=w_f), X[rows])
        for mode in MODES:
            scores[mode][name] = p[mode]
        for mode in spec:
            shown = np.where(c, p[mode], np.median(p[mode][fit & c]))
            spec[mode][name] = auc(y[test], shown[test])

    def stacked(mode: str, kind: str) -> dict:
        S = _stack_matrix(scores[mode], cov, stack, kind)
        q = predict(fit_logistic(S[stack], y[stack], epochs=epochs, lr=0.1), S)
        return {"auc": auc(y[test], q[test]), "recall5": recall_at_top(y[test], q[test]),
                "per_merchant": {nm: _safe_auc(y[test & (vert == code)], q[test & (vert == code)])
                                 for code, nm in merchants}}

    return {
        "rounds": rounds, "local_epochs": local_epochs,
        "merchants": {nm: {"fit_rows": int(((vert == code) & fit).sum()),
                           "test_rows": int(((vert == code) & test).sum()),
                           "test_fraud": int(y[test & (vert == code)].sum())} for code, nm in merchants},
        "stack_scores": {mode: stacked(mode, "scores") for mode in MODES},
        "stack_bands": {mode: stacked(mode, "bands") for mode in MODES},
        "specialist_auc": spec,
    }


def report(res: dict) -> str:
    names = list(res["merchants"])
    L = [f"FedAvg: {res['rounds']} rounds x {res['local_epochs']} local epochs; specialists are logistic; "
         "stacker pooled\n",
         "merchant (node)    fit rows   test rows   test fraud"]
    for nm, m in res["merchants"].items():
        L.append(f"  {nm:14s}{m['fit_rows']:10,d}{m['test_rows']:12,d}{m['test_fraud']:12,d}")
    for kind, title in (("stack_scores", "STACK of scores"), ("stack_bands", "STACK of bands only")):
        L.append(f"\n{title}: AUC on the test period, by how the specialists were trained")
        L.append(f"{'training':16s}{'all':>8s}{'catch@5%':>10s}" + "".join(f"{n:>8s}" for n in names))
        for mode in MODES:
            r = res[kind][mode]
            L.append(f"{mode:16s}{r['auc']:8.3f}{r['recall5']:10.3f}"
                     + "".join(f"{r['per_merchant'][n]:8.3f}" for n in names))
    L.append("\nSingle specialists, AUC overall: central vs federated")
    for fam_name, a in sorted(res["specialist_auc"]["central"].items(), key=lambda kv: -kv[1]):
        b = res["specialist_auc"]["federated"][fam_name]
        L.append(f"  {fam_name:14s}{a:8.3f}{b:8.3f}   (federated {b - a:+.3f})")
    return "\n".join(L)
