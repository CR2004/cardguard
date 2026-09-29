"""Train the live specialists and write specialist_weights.json (what merchant nodes load at startup).

    python -m cardguard.specialists.export [--mode personalised|federated] [--out PATH]

Per specialist (transaction, identity, geo, behavior; only features the node can compute at checkout):
FedAvg across the five ProductCD merchants, weights only; then, in `personalised` mode, each merchant fine-tunes
for a few epochs on its own rows. Band cuts are ONE set per model, from the pooled stack-period scores (about 5%
high, the next 15% medium), like the existing model_risk_band: a "high" then means the same absolute risk at every
merchant. Per-merchant cuts would force ~5% high everywhere and erase that fraud rates differ about 5x across
merchants (pooled AUC fell from 0.77 to 0.59 when tried). The stacker is logistic regression on the four bands,
trained on the stack period. Nothing here touches the test period; the reported metrics are on it. Needs
datasets/ (see cardguard.specialists.experiment).
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from cardguard import ROOT
from cardguard.specialists import data as sdata
from cardguard.specialists.experiment import _load, _split
from cardguard.specialists.federated import (FINETUNE_EPOCHS, FINETUNE_LR, LOCAL_EPOCHS, ROUNDS,
                                             _safe_auc, fedavg_train)
from cardguard.specialists.features import build_families, restrict
from cardguard.specialists.live import STACK_ORDER
from cardguard.specialists.model import auc, band_cuts, fit_logistic, predict, recall_at_top, to_bands

OUT = ROOT / "specialist_weights.json"


def _r(a) -> list:
    return [round(float(x), 6) for x in np.asarray(a).ravel()]


def train_live(fam: dict, mode: str = "personalised", rounds: int = ROUNDS, local_epochs: int = LOCAL_EPOCHS,
               epochs: int = 300) -> dict:
    fam = restrict(fam)
    y, vert = fam["y"], fam["vert"]
    fit, stack, test = _split(fam)
    verts = [(code, nm) for code, nm in enumerate(fam["vert_names"]) if (vert == code).any()]
    n = len(y)
    out = {"version": 1, "source": "ieee-cis", "mode": mode, "rounds": rounds, "local_epochs": local_epochs,
           "caps": {"prior_count": fam["caps"]["prior_count"]}, "specialists": {}}
    bands = {}  # name -> per-row band index, using the cuts of the row's own merchant

    for name in STACK_ORDER:
        f = fam["families"][name]
        X = f["X"]
        parts = {c: (X[(vert == c) & fit], y[(vert == c) & fit]) for c, _ in verts}
        w_g = fedavg_train(list(parts.values()), X.shape[1], rounds, local_epochs)
        p_glob = predict(w_g, X)
        p_mix, per = p_glob.copy(), {}
        for c, nm in verts:
            Xm, ym = parts[c]
            w = fit_logistic(Xm, ym, epochs=FINETUNE_EPOCHS, lr=FINETUNE_LR, init=w_g) if mode == "personalised" else w_g
            p_mix[vert == c] = predict(w, X[vert == c])
            per[nm] = w
        cuts = band_cuts(p_mix[stack])  # one set for every merchant
        bands[name] = to_bands(p_mix, cuts)
        out["specialists"][name] = {
            "features": f["names"],
            "global": {"weights": _r(w_g), "cuts": _r(band_cuts(p_glob[stack]))},
            "verticals": {nm: {"weights": _r(w), "cuts": _r(cuts)} for nm, w in per.items()}}

    Z = np.column_stack([(bands[nm] == k).astype(float) for nm in STACK_ORDER for k in (1, 2)])
    w_s = fit_logistic(Z[stack], y[stack], epochs=epochs, lr=0.1)
    q = predict(w_s, Z)
    stack_cuts = band_cuts(q[stack])
    out["stack"] = {"order": list(STACK_ORDER), "weights": _r(w_s), "global": {"cuts": _r(stack_cuts)},
                    "verticals": {nm: {"cuts": _r(stack_cuts)} for _, nm in verts}}
    sb = to_bands(q, stack_cuts)
    out["metrics"] = {
        "test_auc_stack_score": round(auc(y[test], q[test]), 4),
        "test_auc_stack_band": round(auc(y[test], sb[test].astype(float)), 4),
        "test_catch_top5": round(recall_at_top(y[test], q[test]), 4),
        "test_auc_by_merchant": {nm: round(_safe_auc(y[test & (vert == c)], q[test & (vert == c)]), 4)
                                 for c, nm in verts}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["personalised", "federated"], default="personalised")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if os.path.exists(sdata.CACHE):
        fam = _load()
    elif sdata.available():
        fam = build_families(sdata.load_raw())
    else:
        raise SystemExit("datasets/train_transaction.csv not found; see datasets/README.md")
    spec = train_live(fam, mode=a.mode)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    m = spec["metrics"]
    print(f"wrote {a.out} ({a.mode}): test AUC stack score {m['test_auc_stack_score']}, "
          f"banded {m['test_auc_stack_band']}, catch@5% {m['test_catch_top5']}")
    print("by merchant:", m["test_auc_by_merchant"])


if __name__ == "__main__":
    main()
