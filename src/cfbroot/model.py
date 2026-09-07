"""Turning power ratings into game win probabilities, and calibrating them."""

from __future__ import annotations

from dataclasses import dataclass, replace

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
    game results around the spread.
    """
    mu = projected_margin(rating_home, rating_away, neutral, params)
    return sps.norm.cdf(mu / params.sigma)


@dataclass
class Calibration:
    """Result of re-fitting the outcome model on this season's finished games."""

    n_games: int
    slope: float
    hfa: float
    sigma: float
    slope_raw: float
    hfa_raw: float
    sigma_raw: float
    shrink_weight: float
    brier: float = float("nan")
    log_loss: float = float("nan")

    def summary(self) -> str:
        if self.n_games == 0:
            return "No completed games yet -- using prior model parameters."
        return (
            f"Calibrated on {self.n_games} completed games: "
            f"slope {self.slope:.3f}, HFA {self.hfa:.2f} pts, sigma {self.sigma:.2f} pts "
            f"(raw fit {self.slope_raw:.3f}/{self.hfa_raw:.2f}/{self.sigma_raw:.2f}, "
            f"shrunk {1 - self.shrink_weight:.0%} toward prior)."
        )


def calibrate(rating_home, rating_away, neutral, margin, params: ModelParams,
              prior_strength: float = 150.0) -> tuple[ModelParams, Calibration]:
    """Re-fit slope, home-field advantage and residual SD on completed games.

    Early in the season there are too few games to trust a raw fit, so the
    estimates are shrunk toward the priors in ``params`` with a weight of
    ``n / (n + prior_strength)``. By November the data dominates; in week 2 the
    prior does.
    """
    rating_home = np.asarray(rating_home, dtype=np.float64)
    rating_away = np.asarray(rating_away, dtype=np.float64)
    neutral = np.asarray(neutral, dtype=bool)
    margin = np.asarray(margin, dtype=np.float64)

    ok = np.isfinite(rating_home) & np.isfinite(rating_away) & np.isfinite(margin)
    rating_home, rating_away, neutral, margin = (
        rating_home[ok], rating_away[ok], neutral[ok], margin[ok])
    n = int(margin.size)

    if n < 25:
        cal = Calibration(n_games=n, slope=params.rating_scale, hfa=params.hfa,
                          sigma=params.sigma, slope_raw=float("nan"),
                          hfa_raw=float("nan"), sigma_raw=float("nan"),
                          shrink_weight=0.0)
        return params, cal

    # margin ~ slope * (rating gap) + hfa * (game is not at a neutral site)
    X = np.column_stack([rating_home - rating_away, (~neutral).astype(np.float64)])
    coef, *_ = np.linalg.lstsq(X, margin, rcond=None)
    slope_raw, hfa_raw = float(coef[0]), float(coef[1])
    resid = margin - X @ coef
    sigma_raw = float(np.sqrt(resid @ resid / max(n - 2, 1)))

    w = n / (n + prior_strength)
    slope = w * slope_raw + (1 - w) * params.rating_scale
    hfa = w * hfa_raw + (1 - w) * params.hfa
    sigma = w * sigma_raw + (1 - w) * params.sigma
    # A degenerate fit (e.g. a week of blowouts) must not produce a nonsense model.
    slope = float(np.clip(slope, 0.3, 2.0))
    hfa = float(np.clip(hfa, -2.0, 8.0))
    sigma = float(np.clip(sigma, 8.0, 30.0))

    tuned = replace(params, rating_scale=slope, hfa=hfa, sigma=sigma)

    p = win_probability(rating_home, rating_away, neutral, tuned)
    y = (margin > 0).astype(np.float64)
    brier = float(np.mean((p - y) ** 2))
    eps = 1e-12
    log_loss = float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))

    cal = Calibration(n_games=n, slope=slope, hfa=hfa, sigma=sigma,
                      slope_raw=slope_raw, hfa_raw=hfa_raw, sigma_raw=sigma_raw,
                      shrink_weight=w, brier=brier, log_loss=log_loss)
    return tuned, cal
