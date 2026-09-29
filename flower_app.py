"""Flower wrapper for the federated fraud model (Flower 1.39 Message API).

Each SuperNode is one merchant and trains only on its own rows: a ProductCD vertical of
IEEE-CIS when data/train_transaction.csv is present (5 nodes), else a synthetic merchant
(3 nodes). The ServerApp runs FedAvg and saves the global weights to fl_weights.json
(and .npy), which the merchant node loads to produce the `model_risk_band` fact.

Local simulation:
    python flower_app.py
"""
from __future__ import annotations

import json

import numpy as np
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

import fl
import fl_data

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
    content = RecordDict({"arrays": ArrayRecord([w]),
                          "metrics": MetricRecord({"num-examples": n})})
    return Message(content=content, reply_to=msg)


@server.main()
def main(grid: Grid, context: Context) -> None:
    n = len(NODES)
    strategy = FedAvg(fraction_evaluate=0.0, min_train_nodes=n, min_available_nodes=n)
    result = strategy.start(grid=grid, initial_arrays=ArrayRecord([fl.init_weights()]),
                            num_rounds=ROUNDS)
    w = result.arrays.to_numpy_ndarrays()[0]
    np.save("fl_weights.npy", w)
    with open("fl_weights.json", "w") as f:  # Flower Hub allows .json, not .npy
        json.dump({"source": "ieee-cis" if REAL else "synthetic", "features": fl.FEATURES,
                   "weights": [float(x) for x in w], "band_cuts": list(fl.BAND_CUTS),
                   "nodes": list(NODES), "rounds": ROUNDS}, f, indent=1)
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
