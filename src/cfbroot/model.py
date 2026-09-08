"""Turning power ratings into game win probabilities.

The parameters are fixed, and derived offline from closing betting lines rather
than re-fit during the season. See :func:`provenance` for where each number
comes from and :mod:`cfbroot.calibration` for the script that produced them.

Re-fitting in season is the obvious thing to do and it is wrong. FPI is updated
*after* each week's games, so a fit of "current ratings against already-played
games" is scored on results the ratings have already absorbed. The estimate is
biased low no matter how much data accumulates: on the 2025 season it produced
a residual SD of 13.2, below the 15.3 achieved by a sharp closing line, which
is impossible for a strictly worse forecaster.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

from .config import ModelParams


def projected_margin(rating_home, rating_away, neutral, params: ModelParams):
    """Expected home margin of victory, in points."""
    rating_home = np.asarray(rating_home, dtype=np.float64)
    rating_away = np.asarray(rating_away, dtype=np.float64)
    neutral = np.asarray(neutral, dtype=bool)
    edge = params.rating_scale * (rating_home - rating_away)
    return edge + np.where(neutral, 0.0, params.hfa)


def win_probability(rating_home, rating_away, neutral, params: ModelParams):
    """P(home team wins).

    FPI is a points-above-average rating, so the rating gap is already on the
    scale of a point spread. Converting a spread to a win probability with a
    normal CDF is the standard closing-line approach; ``sigma`` is the SD of
    results around that spread.
    """
    mu = projected_margin(rating_home, rating_away, neutral, params)
    return sps.norm.cdf(mu / params.sigma)


@dataclass
class Diagnostics:
    """How the fixed model scores against games that have been played.

    Reported, never fed back. These numbers flatter the model for the same
    reason the old calibration was biased: the ratings already know how these
    games turned out.
    """

    n_games: int
    brier: float = float("nan")
    log_loss: float = float("nan")
    accuracy: float = float("nan")
    mean_abs_margin_error: float = float("nan")

    def summary(self) -> str:
        if self.n_games < 10:
            return ("Model parameters are fixed from a historical calibration; "
                    "too few games played to report a fit.")
        return (f"Fixed parameters. Against {self.n_games} completed games: "
                f"Brier {self.brier:.3f}, accuracy {self.accuracy:.0%}, mean margin "
                f"error {self.mean_abs_margin_error:.1f} pts (optimistic -- the "
                f"ratings already reflect these results).")


def provenance(params: ModelParams) -> str:
    return (f"slope {params.rating_scale:.2f}, home field {params.hfa:.2f} pts, "
            f"sigma {params.sigma:.2f} pts -- calibrated against "
            f"{params.calibration_n:,} closing betting lines "
            f"({params.calibration_seasons}).")


def evaluate(rating_home, rating_away, neutral, margin,
             params: ModelParams) -> Diagnostics:
    """Score the fixed model on completed games. Does not change ``params``."""
    rating_home = np.asarray(rating_home, dtype=np.float64)
    rating_away = np.asarray(rating_away, dtype=np.float64)
    neutral = np.asarray(neutral, dtype=bool)
    margin = np.asarray(margin, dtype=np.float64)

    ok = np.isfinite(rating_home) & np.isfinite(rating_away) & np.isfinite(margin)
    rating_home, rating_away = rating_home[ok], rating_away[ok]
    neutral, margin = neutral[ok], margin[ok]
    n = int(margin.size)
    if n == 0:
        return Diagnostics(n_games=0)

    mu = projected_margin(rating_home, rating_away, neutral, params)
    p = np.asarray(win_probability(rating_home, rating_away, neutral, params))
    y = (margin > 0).astype(np.float64)
    eps = 1e-12
    return Diagnostics(
        n_games=n,
        brier=float(np.mean((p - y) ** 2)),
        log_loss=float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))),
        accuracy=float(np.mean((p > 0.5) == (y > 0.5))),
        mean_abs_margin_error=float(np.mean(np.abs(margin - mu))),
    )
