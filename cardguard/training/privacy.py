"""Differential privacy on the federated round, with a spent-budget report.

Flower's DifferentialPrivacyServerSideFixedClipping clips every node's update to a fixed norm and
adds Gaussian noise before averaging, so a single merchant's rows cannot be read back out of the
weights. Its RDP accountant (Google dp-accounting under the hood) turns noise multiplier, rounds
and population into a cumulative (epsilon, delta): the number a judge asks for.
"""
from __future__ import annotations

import os

from flwr.serverapp.strategy import DifferentialPrivacyServerSideFixedClipping
from flwr.supercore.privacy_accounting import NeighboringRelation, PrivacyConfig, SamplingMethod
from flwr.supercore.privacy_accounting.rdp_accountant import RdpAccountant

NOISE_MULTIPLIER = float(os.environ.get("FL_DP_NOISE", "0"))   # 0 = DP off
CLIPPING_NORM = float(os.environ.get("FL_DP_CLIP", "1.0"))
TARGET_DELTA = 1e-5


def enabled() -> bool:
    return NOISE_MULTIPLIER > 0


def wrap(strategy, num_nodes: int, noise_multiplier: float = NOISE_MULTIPLIER, clipping_norm: float = CLIPPING_NORM):
    """FedAvg -> DP FedAvg with client-level accounting over the federation's nodes."""
    accountant = RdpAccountant(PrivacyConfig(target_delta=TARGET_DELTA, population_size=num_nodes,
                                             neighboring_relation=NeighboringRelation.ADD_OR_REMOVE_ONE,
                                             sampling_method=SamplingMethod.NO_AMPLIFICATION))
    return DifferentialPrivacyServerSideFixedClipping(strategy, noise_multiplier=noise_multiplier,
                                                      clipping_norm=clipping_norm, num_sampled_clients=num_nodes,
                                                      accountant=accountant)


def spent(strategy) -> dict | None:
    """The cumulative budget after training, as plain numbers for the UI and fl_weights.json."""
    ps = getattr(strategy, "privacy_spent", lambda: None)()
    if ps is None:
        return None
    return {"epsilon": round(ps.epsilon, 3), "delta": ps.delta, "releases": ps.num_releases,
            "method": ps.accounting_method, "noise_multiplier": strategy.noise_multiplier,
            "clipping_norm": strategy.clipping_norm}
