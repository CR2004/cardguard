"""EXPERIMENT (branch specialists-experiment): fraud specialists, each learning one signal family.

Not wired into the demo. The live pipeline (fl.FEATURES, fl_weights.json, merchant.py, BAND_CUTS)
is unchanged. This package answers one question offline: does a stack of specialists, each seeing
only its own columns, beat every single specialist? Run it with

    python -m cardguard.specialists.experiment            # real IEEE-CIS, needs datasets/*.csv
    python -m cardguard.specialists.experiment --synthetic

This is vertical (feature-partitioned) federation plus stacking, not FedAvg: the specialists hold
different columns of the SAME transactions, and only a banded score would cross the wire.
"""
