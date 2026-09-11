"""Confidence intervals and multiple-testing control for simulated probabilities."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

__all__ = [
    "wilson_ci",
    "newcombe_diff_ci",
    "prop_diff_pvalue",
    "benjamini_hochberg",
    "mc_stderr",
    "sims_for_resolution",
    "Estimate",
    "DiffEstimate",
]


def _z(alpha: float) -> float:
    return float(sps.norm.ppf(1.0 - alpha / 2.0))


def wilson_ci(k, n, alpha: float = 0.05):
    """Wilson score interval for a binomial proportion. Stays sensible near 0 and 1."""
    k = np.asarray(k, dtype=np.float64)
    n = np.asarray(n, dtype=np.float64)
    z = _z(alpha)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(n > 0, k / n, np.nan)
        denom = 1.0 + z * z / n
        centre = (p + z * z / (2.0 * n)) / denom
        half = (z / denom) * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
        lo = np.clip(centre - half, 0.0, 1.0)
        hi = np.clip(centre + half, 0.0, 1.0)
    lo = np.where(n > 0, lo, np.nan)
    hi = np.where(n > 0, hi, np.nan)
    if np.isscalar(k) or getattr(k, "ndim", 0) == 0:
        return float(lo), float(hi)
    return lo, hi


def newcombe_diff_ci(k1, n1, k2, n2, alpha: float = 0.05):
    """Newcombe (method 10) interval for p1 - p2, built from two Wilson intervals.

    Holds up when one side is a small share of the simulations, as with a big
    underdog's upset branch.
    """
    k1 = np.asarray(k1, dtype=np.float64)
    n1 = np.asarray(n1, dtype=np.float64)
    k2 = np.asarray(k2, dtype=np.float64)
    n2 = np.asarray(n2, dtype=np.float64)

    l1, u1 = wilson_ci(k1, n1, alpha)
    l2, u2 = wilson_ci(k2, n2, alpha)
    with np.errstate(invalid="ignore", divide="ignore"):
        p1 = np.where(n1 > 0, k1 / n1, np.nan)
        p2 = np.where(n2 > 0, k2 / n2, np.nan)
    diff = p1 - p2
    lo = diff - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = diff + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    lo = np.clip(lo, -1.0, 1.0)
    hi = np.clip(hi, -1.0, 1.0)
    if np.ndim(diff) == 0:
        return float(diff), float(lo), float(hi)
    return diff, lo, hi


def prop_diff_pvalue(k1, n1, k2, n2):
    """Two-sided p-value for H0: p1 == p2 (pooled two-proportion z-test)."""
    k1 = np.asarray(k1, dtype=np.float64)
    n1 = np.asarray(n1, dtype=np.float64)
    k2 = np.asarray(k2, dtype=np.float64)
    n2 = np.asarray(n2, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        pooled = (k1 + k2) / (n1 + n2)
        se = np.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
        z = (k1 / n1 - k2 / n2) / se
    p = 2.0 * sps.norm.sf(np.abs(z))
    # An empty or degenerate side gives p = 1.
    p = np.where(np.isfinite(p), p, 1.0)
    if np.ndim(p) == 0:
        return float(p)
    return p


def benjamini_hochberg(pvalues, q: float = 0.05):
    """Benjamini-Hochberg false discovery rate control. Returns (rejected, qvalues)."""
    p = np.asarray(pvalues, dtype=np.float64).ravel()
    n = p.size
    if n == 0:
        return np.zeros(0, dtype=bool), np.zeros(0)
    order = np.argsort(p)
    ranked = p[order]
    qvals_sorted = ranked * n / np.arange(1, n + 1)
    # enforce monotonicity from the largest p downward
    qvals_sorted = np.minimum.accumulate(qvals_sorted[::-1])[::-1]
    qvals_sorted = np.clip(qvals_sorted, 0.0, 1.0)
    qvals = np.empty_like(qvals_sorted)
    qvals[order] = qvals_sorted
    return qvals <= q, qvals


def mc_stderr(p, n):
    """Monte Carlo standard error of a simulated probability."""
    p = np.asarray(p, dtype=np.float64)
    n = np.asarray(n, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.sqrt(p * (1.0 - p) / n)
    return float(out) if np.ndim(out) == 0 else out


def sims_for_resolution(delta: float, p: float = 0.5, alpha: float = 0.05,
                        power: float = 0.8) -> float:
    """Simulations needed to detect a swing of ``delta``, assuming an even split."""
    if delta <= 0:
        return math.inf
    za = _z(alpha)
    zb = float(sps.norm.ppf(power))
    var = 2.0 * p * (1.0 - p)
    n_per_arm = (za + zb) ** 2 * var / (delta ** 2)
    return 2.0 * n_per_arm


@dataclass
class Estimate:
    """A simulated probability with its uncertainty."""

    p: float
    n: int
    lo: float
    hi: float
    se: float

    @classmethod
    def from_counts(cls, k: int, n: int, alpha: float = 0.05) -> "Estimate":
        p = k / n if n else float("nan")
        lo, hi = wilson_ci(k, n, alpha)
        return cls(p=p, n=int(n), lo=lo, hi=hi, se=mc_stderr(p, n) if n else float("nan"))


@dataclass
class DiffEstimate:
    """The swing in a metric between the two outcomes of one game."""

    p_if_home: float
    p_if_away: float
    n_home: int
    n_away: int
    delta: float
    lo: float
    hi: float
    pvalue: float
    qvalue: float = 1.0
    significant: bool = False
