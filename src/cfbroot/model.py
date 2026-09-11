"""Win probabilities from power ratings.

The parameters are fixed (see cfbroot.calibration). Fitting them on games that
have already been played gives a biased answer, because FPI is updated after
those games and already reflects their results.
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
    """P(home team wins): normal CDF of the projected margin divided by sigma."""
    mu = projected_margin(rating_home, rating_away, neutral, params)
    return sps.norm.cdf(mu / params.sigma)


@dataclass
class Diagnostics:
    """How the fixed model scores on completed games.

    Reported only, never used to change the parameters. The numbers look
    better than they should, since the ratings already include these games.
    """

    n_games: int
    brier: float = float("nan")
    log_loss: float = float("nan")
    accuracy: float = float("nan")
    mean_abs_margin_error: float = float("nan")

    def summary(self) -> str:
        if self.n_games < 10:
            return ("Parameters are fixed from a historical calibration. "
                    "Too few completed games to report a fit.")
        return (f"Fixed parameters. {self.n_games} completed games: "
                f"Brier {self.brier:.3f}, accuracy {self.accuracy:.0%}, mean margin "
                f"error {self.mean_abs_margin_error:.1f} pts (optimistic, since the "
                f"ratings already include these games).")


def provenance(params: ModelParams) -> str:
    return (f"slope {params.rating_scale:.2f}, home field {params.hfa:.2f} pts, "
            f"sigma {params.sigma:.2f} pts, from {params.calibration_n:,} closing "
            f"betting lines ({params.calibration_seasons}).")


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
