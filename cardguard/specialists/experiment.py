"""Measure the live specialists trained merchant by merchant (see federated.py for the protocol).

    python -m cardguard.specialists.experiment [--synthetic] [--rebuild] [--limit N]

Needs datasets/train_transaction.csv (see datasets/README.md); --synthetic runs offline on generated data. The
first real run builds datasets/specialist_features.npz (about 2 minutes) and later runs reuse it.
"""
from __future__ import annotations

import argparse
import os

import numpy as np

from cardguard.specialists import data as sdata
from cardguard.specialists import federated
from cardguard.specialists.features import FAMILIES, build_families


def _save(fam: dict) -> None:
    z = {"y": fam["y"], "vert": fam["vert"], "is_test": fam["is_test"], "dt": fam["dt"],
         "vert_names": np.array(fam["vert_names"]), "cap_prior_count": np.array(fam["caps"]["prior_count"])}
    for n, f in fam["families"].items():
        z[f"X_{n}"], z[f"names_{n}"] = f["X"], np.array(f["names"])
    np.savez_compressed(sdata.CACHE, **z)


def _load() -> dict:
    z = np.load(sdata.CACHE, allow_pickle=False)
    if any(f"X_{n}" not in z.files for n in FAMILIES):
        raise SystemExit("datasets/specialist_features.npz is from an older version; rerun with --rebuild")
    return {"families": {n: {"X": z[f"X_{n}"], "names": [str(s) for s in z[f"names_{n}"]]} for n in FAMILIES},
            "y": z["y"], "vert": z["vert"], "is_test": z["is_test"], "dt": z["dt"],
            "vert_names": [str(s) for s in z["vert_names"]],
            "caps": {"prior_count": float(z["cap_prior_count"])}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="offline demo data, four injected fraud types")
    ap.add_argument("--rebuild", action="store_true", help="ignore the feature cache")
    ap.add_argument("--limit", type=int, help="read only the first N CSV rows (no cache)")
    a = ap.parse_args()
    if a.synthetic:
        fam = build_families(sdata.synthetic_raw())
    elif os.path.exists(sdata.CACHE) and not a.rebuild and a.limit is None:
        fam = _load()
    elif sdata.available():
        fam = build_families(sdata.load_raw(limit=a.limit))
        if a.limit is None:
            _save(fam)
    else:
        raise SystemExit(f"{sdata.TX_CSV} not found. Put train_transaction.csv in datasets/, or run with --synthetic.")
    print(f"rows {len(fam['y']):,}  fraud {fam['y'].mean():.2%}  test rows {int(fam['is_test'].sum()):,}\n")
    print(federated.report(federated.run_federated(fam, with_original=not a.synthetic)))


if __name__ == "__main__":
    main()
