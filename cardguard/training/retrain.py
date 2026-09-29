"""Self-improving loop: human decisions become labels; a federated round spreads the lesson.

Every human review (approve = legitimate, decline = fraud) and every chargeback on an approved
payment becomes a labelled example ON THE MERCHANT'S NODE: the nine local features and a 0/1.
`federated_round()` then runs FedAvg across every registered node (the same maths as
flower_app.py; each node trains on its own rows plus its own human labels, weighted), and the
merchant finishes with a few local epochs on its human labels alone (personalisation), so a
pattern a human flagged twice is caught next time. Only weights move between nodes.

A node registry lets a new merchant join with one command (`python -m cardguard.training.join`).
"""
from __future__ import annotations

import numpy as np

from cardguard.data import ieee_cis as fl_data
from cardguard.training import fl

HUMAN_LABEL_WEIGHT = 50.0   # one human decision is worth this many ordinary rows in the round
FINETUNE_EPOCHS = 40
FINETUNE_LR = 0.5
ROUNDS = 10


class Registry:
    """Nodes in the federation: name -> data source (a real vertical or a synthetic merchant)."""

    def __init__(self):
        self.nodes: dict[str, str] = {}
        self._rows: dict = {}
        real = fl_data.available()
        for name in (fl_data.VERTICALS if real else fl.MERCHANTS):
            self.nodes[f"node-{name}"] = name

    def sources(self) -> set[str]:
        return (set(fl_data.VERTICALS) if fl_data.available() else set()) | set(fl.MERCHANTS)

    MAX_NODES = 50

    def join(self, name: str, source: str) -> dict:
        if source not in self.sources():
            raise ValueError("unknown data source")
        if name not in self.nodes and len(self.nodes) >= self.MAX_NODES:
            raise ValueError("federation is full")
        self.nodes[name] = source
        return {"nodes": len(self.nodes), "joined": name, "source": source}

    def rows(self, source: str):
        """This node's rows. Real slices come from the feature cache (loaded once); synthetic merchants
        get their own seed so no two nodes hold identical data."""
        if source not in self._rows:
            if source in fl_data.VERTICALS and fl_data.available():
                self._rows[source] = fl_data.split(fl_data.load(), source)
            else:
                merchant = source if source in fl.MERCHANTS else fl.MERCHANTS[0]
                self._rows[source] = fl.make_merchant_data(merchant, n=4000, seed=fl.MERCHANTS.index(merchant))
        return self._rows[source]


def _with_labels(X, y, labels: list[tuple[list[float], int]]):
    """This node's rows plus its human labels, the labels weighted HUMAN_LABEL_WEIGHT each."""
    if not labels:
        return X, y, None
    Xl = np.array([f for f, _ in labels], dtype=float)
    yl = np.array([lab for _, lab in labels], dtype=float)
    sw = np.r_[np.ones(len(y)), np.full(len(yl), HUMAN_LABEL_WEIGHT)]
    return np.vstack([X, Xl]), np.r_[y, yl], sw


def federated_round(registry: Registry, my_node: str, my_labels: list, weights: np.ndarray,
                    rounds: int = ROUNDS) -> np.ndarray:
    """FedAvg over every registered node, starting from the current global weights."""
    w = weights.copy()
    for _ in range(rounds):
        results = []
        for name, source in registry.nodes.items():
            X, y = registry.rows(source)
            sw = None
            if name == my_node:
                X, y, sw = _with_labels(X, y, my_labels)
            results.append(fl.local_train(w, X, y, epochs=1, sample_weight=sw))
        w = fl.fedavg(results)
    return w


def finetune(weights: np.ndarray, labels: list) -> np.ndarray:
    """Personalisation: a few local epochs on this node's human labels only."""
    if not labels:
        return weights
    X = np.array([f for f, _ in labels], dtype=float)
    y = np.array([lab for _, lab in labels], dtype=float)
    w, _ = fl.local_train(weights, X, y, epochs=FINETUNE_EPOCHS, lr=FINETUNE_LR, pos_weight=1.0)
    return w


def retrain(registry: Registry, my_node: str, my_labels: list, weights: np.ndarray) -> dict:
    """One federated round plus personalisation. Returns new weights and before/after for the labels."""
    before = [fl.risk_band(float(fl.predict_proba(weights, np.array([f]))[0])) for f, _ in my_labels]
    global_w = federated_round(registry, my_node, my_labels, weights)
    local_w = finetune(global_w, my_labels)
    after = [fl.risk_band(float(fl.predict_proba(local_w, np.array([f]))[0])) for f, _ in my_labels]
    return {"weights": local_w, "nodes": len(registry.nodes), "labels": len(my_labels), "before": before, "after": after}
