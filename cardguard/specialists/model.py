"""Logistic regression with Adam, banding, and the metrics the experiment reports.

Adam (not fl.local_train's plain gradient step) because the stacker's inputs are not all in [0, 1]
and the C-count features are skewed; it converges without hand-tuning the learning rate per family.
"""
from __future__ import annotations

import numpy as np

from cardguard.training.fl import auc  # Mann-Whitney AUC with tie handling

BAND_NAMES = ("low", "medium", "high")  # what would cross the wire: never the raw score


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X: np.ndarray, y: np.ndarray, epochs: int = 300, lr: float = 0.05,
                 pos_weight: float = 10.0, l2: float = 1e-4) -> np.ndarray:
    """Weights [bias, w1..wd]. Fraud is rare, so positives are upweighted."""
    w, m, v = (np.zeros(X.shape[1] + 1) for _ in range(3))
    sw = np.where(y == 1, pos_weight, 1.0)
    for t in range(1, epochs + 1):
        err = (predict(w, X) - y) * sw
        g = np.r_[err.mean(), X.T @ err / len(y) + l2 * w[1:]]
        m = 0.9 * m + 0.1 * g
        v = 0.999 * v + 0.001 * g * g
        w -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return sigmoid(X @ w[1:] + w[0])


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def band_cuts(scores: np.ndarray, medium_share: float = 0.20, high_share: float = 0.05) -> tuple:
    """Score cuts so that ~5% of the reference scores are 'high' and the next ~15% 'medium'."""
    return (float(np.quantile(scores, 1 - medium_share)), float(np.quantile(scores, 1 - high_share)))


def to_bands(scores: np.ndarray, cuts: tuple) -> np.ndarray:
    """0 = low, 1 = medium, 2 = high (index into BAND_NAMES)."""
    return (scores >= cuts[0]).astype(int) + (scores >= cuts[1]).astype(int)


def recall_at_top(y: np.ndarray, p: np.ndarray, share: float = 0.05) -> float:
    """Share of all fraud that lands in the top `share` of scores (ties broken arbitrarily)."""
    k = max(int(len(p) * share), 1)
    top = np.argsort(-p, kind="stable")[:k]
    return float(y[top].sum() / max(y.sum(), 1))


__all__ = ["BAND_NAMES", "auc", "band_cuts", "fit_logistic", "logit", "predict", "recall_at_top",
           "sigmoid", "to_bands"]
