"""Flower wrapper for the federated fraud model (Flower 1.39 Message API).

Each SuperNode is one merchant and trains only on its own rows: a ProductCD vertical of
IEEE-CIS when datasets/train_transaction.csv is present (5 nodes), else a synthetic merchant
(3 nodes). The ServerApp runs FedAvg and saves the global weights to fl_weights.json
, which the merchant node loads to produce the `model_risk_band` fact.

Local simulation:
    python -m cardguard.training.flower_app
"""
from __future__ import annotations

import json
import os

import numpy as np

from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg, FedMedian

from cardguard import ROOT
from cardguard.data import ieee_cis as fl_data
from cardguard.training import fl, privacy

ROUNDS = 30
REAL = fl_data.available()
NODES = fl_data.VERTICALS if REAL else fl.MERCHANTS
client = ClientApp()
server = ServerApp()


def local_rows(pid: int):
    """This node's own transactions. Rows never leave the node; only weights do."""
    name = NODES[pid % len(NODES)]
    if REAL:
        return name, fl_data.split(fl_data.load(), name)
    return name, fl.make_merchant_data(name, seed=pid)


@client.train()
def train(msg: Message, context: Context) -> Message:
    pid = int(context.node_config.get("partition-id", 0))
    merchant, (X, y) = local_rows(pid)
    w = msg.content["arrays"].to_numpy_ndarrays()[0]
    w, n = fl.local_train(w, X, y)
    # Client-level DP accounting requires equal node weights, so DP mode averages nodes equally.
    content = RecordDict({"arrays": ArrayRecord([w]),
                          "metrics": MetricRecord({"num-examples": 1 if privacy.enabled() else n})})
    return Message(content=content, reply_to=msg)


@server.main()
def main(grid: Grid, context: Context) -> None:
    n = len(NODES)
    # FL_ROBUST=1: coordinate-wise median instead of the mean, so one hostile node cannot dominate.
    base = FedMedian if os.environ.get("FL_ROBUST") == "1" else FedAvg
    strategy = base(fraction_evaluate=0.0, min_train_nodes=n, min_available_nodes=n)
    if privacy.enabled():  # clip + Gaussian noise on every update, with an RDP privacy accountant
        strategy = privacy.wrap(strategy, n)
    result = strategy.start(grid=grid, initial_arrays=ArrayRecord([fl.init_weights()]),
                            num_rounds=ROUNDS)
    dp = privacy.spent(strategy)
    if dp:
        print(f"differential privacy: epsilon={dp['epsilon']} at delta={dp['delta']} after {dp['releases']} releases "
              f"(noise {dp['noise_multiplier']}, clip {dp['clipping_norm']})")
    w = result.arrays.to_numpy_ndarrays()[0]
    if not np.all(np.isfinite(w)) or len(w) != len(fl.FEATURES) + 1:
        raise SystemExit("aggregated weights are not finite or have the wrong shape: refusing to save them")
    with open(ROOT / "fl_weights.json", "w") as f:  # Flower Hub allows .json, not .npy
        json.dump({"source": "ieee-cis" if REAL else "synthetic", "features": fl.FEATURES,
                   "weights": [float(x) for x in w], "band_cuts": list(fl.BAND_CUTS),
                   "nodes": list(NODES), "rounds": ROUNDS, "dp": dp}, f, indent=1)
    if REAL:
        data = fl_data.load()
        for v in fl_data.VERTICALS:
            Xt, yt = fl_data.split(data, v, test=True)
            print(f"federated AUC on {v} held-out  {fl.auc(yt, fl.predict_proba(w, Xt)):.3f}")
    else:
        for name, rate in fl.catch_rates(w).items():
            print(f"federated catch rate  {name:14s} {rate:.3f}")


if __name__ == "__main__":
    from flwr.simulation import run_simulation
    print(f"training on {'IEEE-CIS verticals' if REAL else 'synthetic merchants'}: {NODES}")
    run_simulation(server_app=server, client_app=client, num_supernodes=len(NODES))
