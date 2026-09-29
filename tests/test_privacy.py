"""Differential privacy wrapper and its budget report."""
from flwr.serverapp.strategy import FedAvg
from flwr.supercore.privacy_accounting import GaussianPrivacyEvent

from cardguard.training import privacy


def test_wrap_and_budget_grows_with_releases():
    s = privacy.wrap(FedAvg(fraction_evaluate=0.0), num_nodes=5, noise_multiplier=1.0, clipping_norm=1.0)
    assert privacy.spent(s)["releases"] == 0
    ev = GaussianPrivacyEvent(noise_multiplier=1.0, sample_size=5, population_size=5)
    s.accountant.compose(ev, count=10)
    ten = privacy.spent(s)
    s.accountant.compose(ev, count=20)
    thirty = privacy.spent(s)
    assert ten["releases"] == 10 and thirty["releases"] == 30
    assert 0 < ten["epsilon"] < thirty["epsilon"] and ten["delta"] == privacy.TARGET_DELTA
    louder = privacy.wrap(FedAvg(fraction_evaluate=0.0), num_nodes=5, noise_multiplier=3.0)
    louder.accountant.compose(GaussianPrivacyEvent(noise_multiplier=3.0, sample_size=5, population_size=5), count=10)
    assert privacy.spent(louder)["epsilon"] < ten["epsilon"]     # more noise, less budget spent


def test_disabled_by_default_and_plain_fedavg_reports_nothing():
    assert privacy.spent(FedAvg(fraction_evaluate=0.0)) is None
