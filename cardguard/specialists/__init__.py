"""Four one-signal-family fraud models (transaction, identity, geo, behavior), trained merchant by merchant.

Each merchant node loads its own vertical's weights from specialist_weights.json (live.py) and scores every
checkout; the four bands are stacked into one banded fact, `specialist_stack_band`. Training and export are
offline (export.py); federated.py and experiment.py measure the ways of training them.

    python -m cardguard.specialists.export            # writes specialist_weights.json
    python -m cardguard.specialists.experiment        # measures local / FedAvg / FedAvg + fine-tune

The FedAvg here is plain numpy simulating the five merchants in one process; it is not run on Flower SuperNodes.
"""
