"""Flower wrapper for the federated fraud model (Flower 1.39 Message API).

Each SuperNode is one merchant and trains only on its own synthetic data.
The ServerApp runs FedAvg and saves the global weights to fl_weights.npy,
which the merchant node loads to produce the `model_risk_band` fact.

Local simulation (3 merchants):
    python flower_app.py
"""
from __future__ import annotations

import numpy as np
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg

import fl

ROUNDS = 30
client = ClientApp()
server = ServerApp()


@client.train()
def train(msg: Message, context: Context) -> Message:
    pid = int(context.node_config.get("partition-id", 0))
    merchant = fl.MERCHANTS[pid % len(fl.MERCHANTS)]
    X, y = fl.make_merchant_data(merchant, seed=pid)  # data never leaves this node
    w = msg.content["arrays"].to_numpy_ndarrays()[0]
    w, n = fl.local_train(w, X, y)
    content = RecordDict({"arrays": ArrayRecord([w]),
                          "metrics": MetricRecord({"num-examples": n})})
    return Message(content=content, reply_to=msg)


@server.main()
def main(grid: Grid, context: Context) -> None:
    strategy = FedAvg(fraction_evaluate=0.0, min_train_nodes=3, min_available_nodes=3)
    result = strategy.start(grid=grid, initial_arrays=ArrayRecord([fl.init_weights()]),
                            num_rounds=ROUNDS)
    w = result.arrays.to_numpy_ndarrays()[0]
    np.save("fl_weights.npy", w)
    for name, rate in fl.catch_rates(w).items():
        print(f"federated catch rate  {name:14s} {rate:.3f}")


if __name__ == "__main__":
    from flwr.simulation import run_simulation
    run_simulation(server_app=server, client_app=client, num_supernodes=3)
