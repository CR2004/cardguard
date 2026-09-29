"""Do specialists, each seeing one signal family, beat any single one when stacked?

Protocol (time-ordered, no leakage):
  train period = first 80% of the window, test = last 20% (same cut as cardguard.data.ieee_cis)
  fit   = first 70% of the train period: each specialist trains on ITS columns only
  stack = last 30% of the train period: the global agent learns how to weigh specialist scores
  test  = never used for fitting anything
A specialist with no data for a row (device: no identity record) ABSTAINS: it contributes 0 and a
coverage flag, so the stacker can tell "no evidence" from "evidence of nothing".
Two stackers: on raw scores, and on the low/medium/high bands that would actually cross the wire.

    python -m cardguard.specialists.experiment [--synthetic] [--rebuild] [--epochs N] [--limit N]
"""
from __future__ import annotations

import argparse
import os

import numpy as np

from cardguard.data import ieee_cis
from cardguard.specialists import data as sdata
from cardguard.specialists.features import FAMILIES, build_families, restrict
from cardguard.specialists.model import (MODELS, auc, band_cuts, fit_logistic, fit_model, logit, predict,
                                         recall_at_top, to_bands)

FIT_SHARE = 0.70
MIN_ROWS, MIN_FRAUD = 50, 5  # a specialist with less than this has nothing to learn from


def _split(fam: dict):
    dt, is_test = fam["dt"], fam["is_test"]
    train = ~is_test
    cut = np.quantile(dt[train], FIT_SHARE)
    return train & (dt <= cut), train & (dt > cut), is_test


def _stack_matrix(scores, cov, stack, mode, drop=None):
    cols = []
    for name, p in scores.items():
        if name == drop:
            continue
        c = cov[name]
        if mode == "scores":
            cols.append(np.clip(logit(p), -8, 8) / 4 * c)
        else:
            b = to_bands(p, band_cuts(p[stack & c]))
            cols += [(b == 1) * c, (b == 2) * c]
        if not c.all():
            cols.append(c.astype(float))  # coverage flag: "no evidence" vs "evidence of nothing"
    return np.column_stack(cols).astype(float)


def run(fam: dict, epochs: int = 300, model: str = "logistic") -> dict:
    y = fam["y"]
    fit, stack, test = _split(fam)
    scores, cov, single = {}, {}, {}
    for name in FAMILIES:
        if name not in fam["families"]:  # e.g. --deployable drops families with no checkout-computable column
            continue
        f = fam["families"][name]
        X, c = f["X"], f["covered"]
        if (fit & c).sum() < MIN_ROWS or y[fit & c].sum() < MIN_FRAUD:
            continue
        p = fit_model(model, X[fit & c], y[fit & c], epochs=epochs).predict(X)
        scores[name], cov[name] = p, c
        shown = np.where(c, p, np.median(p[fit & c]))  # abstaining rows tie at a neutral score
        single[name] = {"auc": auc(y[test], shown[test]), "recall5": recall_at_top(y[test], shown[test]),
                        "coverage": float(c[test].mean())}

    out = {"model": model, "single": single, "features": {n: len(fam["families"][n]["names"]) for n in scores}}

    def stacked(mode, drop=None):
        S = _stack_matrix(scores, cov, stack, mode, drop)
        w = fit_logistic(S[stack], y[stack], epochs=epochs, lr=0.1)
        p = predict(w, S)
        return {"auc": auc(y[test], p[test]), "recall5": recall_at_top(y[test], p[test])}

    out["stack_scores"], out["stack_bands"] = stacked("scores"), stacked("bands")
    out["ablation"] = {f"without_{n}": stacked("scores", drop=n)["auc"] for n in scores}

    Xall = np.hstack([fam["families"][n]["X"] for n in scores])  # same columns, one model, no privacy split
    p = fit_model(model, Xall[fit], y[fit], epochs=epochs).predict(Xall)
    out["pooled_all_features"] = {"auc": auc(y[test], p[test]), "recall5": recall_at_top(y[test], p[test])}

    if not os.environ.get("SPECIALISTS_SKIP_BASELINE") and ieee_cis.available():
        d = ieee_cis.load()
        if len(d["y"]) == len(y) and np.array_equal(d["is_test"], fam["is_test"]):
            p = fit_model(model, d["X"][fit], y[fit], epochs=epochs).predict(d["X"])
            out["current_9_features"] = {"auc": auc(y[test], p[test]), "recall5": recall_at_top(y[test], p[test])}
    return out


def report(res: dict) -> str:
    L = [f"specialist model: {res['model']}; the stacker is always logistic\n",
         f"{'model':30s}{'AUC':>8s}{'catch@5%':>10s}{'coverage':>10s}{'#feat':>7s}"]
    for name, r in sorted(res["single"].items(), key=lambda kv: -kv[1]["auc"]):
        L.append(f"{'specialist: ' + name:30s}{r['auc']:8.3f}{r['recall5']:10.3f}{r['coverage']:10.2f}"
                 f"{res['features'][name]:7d}")
    for key, label in (("stack_scores", "STACK (scores)"), ("stack_bands", "STACK (bands only)"),
                       ("pooled_all_features", "pooled, one model"), ("current_9_features", "current 9-feature model")):
        if key in res:
            L.append(f"{label:30s}{res[key]['auc']:8.3f}{res[key]['recall5']:10.3f}")
    L.append("\nleave-one-specialist-out (stack of scores), AUC:")
    for k, v in sorted(res["ablation"].items(), key=lambda kv: kv[1]):
        L.append(f"  {k:26s}{v:8.3f}   (drop = {res['stack_scores']['auc'] - v:+.3f})")
    return "\n".join(L)


# ---------- feature cache (real data only; datasets/ is git-ignored) ----------

def _save(fam: dict) -> None:
    z = {"y": fam["y"], "vert": fam["vert"], "is_test": fam["is_test"], "dt": fam["dt"],
         "vert_names": np.array(fam["vert_names"])}
    for n, f in fam["families"].items():
        z[f"X_{n}"], z[f"cov_{n}"], z[f"names_{n}"] = f["X"], f["covered"], np.array(f["names"])
    np.savez_compressed(sdata.CACHE, **z)


def _load() -> dict:
    z = np.load(sdata.CACHE, allow_pickle=False)
    fams = {n: {"X": z[f"X_{n}"], "covered": z[f"cov_{n}"], "names": [str(s) for s in z[f"names_{n}"]]}
            for n in FAMILIES}
    return {"families": fams, "y": z["y"], "vert": z["vert"], "is_test": z["is_test"], "dt": z["dt"],
            "vert_names": [str(s) for s in z["vert_names"]]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="offline demo data, six fraud types")
    ap.add_argument("--rebuild", action="store_true", help="ignore the feature cache")
    ap.add_argument("--deployable", action="store_true",
                    help="only features a live merchant node could compute at checkout")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--model", choices=MODELS, default="logistic", help="what each specialist is")
    ap.add_argument("--limit", type=int, help="read only the first N CSV rows (no cache)")
    a = ap.parse_args()
    if a.synthetic:
        os.environ["SPECIALISTS_SKIP_BASELINE"] = "1"
        fam = build_families(sdata.synthetic_raw())
    elif os.path.exists(sdata.CACHE) and not a.rebuild and a.limit is None:
        fam = _load()
    elif sdata.available():
        fam = build_families(sdata.load_raw(limit=a.limit))
        if a.limit is None:
            _save(fam)
    else:
        raise SystemExit(f"{sdata.TX_CSV} not found. Put train_transaction.csv (and train_identity.csv) "
                         "in datasets/, or run with --synthetic.")
    if a.deployable:
        fam = restrict(fam)
    print(f"rows {len(fam['y']):,}  fraud {fam['y'].mean():.2%}  test rows {int(fam['is_test'].sum()):,}\n")
    print(report(run(fam, epochs=a.epochs, model=a.model)))


if __name__ == "__main__":
    main()
