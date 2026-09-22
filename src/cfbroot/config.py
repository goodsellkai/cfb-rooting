"""Paths, API key handling, and model parameters."""

from __future__ import annotations

import math

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from .selection import (BEST_WIN_BOOST, BEST_WIN_SCALE, COMMITTEE_SD,
                        TITLE_JUMP_MARGIN, WORST_LOSS_BOOST, WORST_LOSS_SCALE)

PKG_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PKG_DIR.parent.parent
CACHE_DIR = Path(os.environ.get("CFBROOT_CACHE", PROJECT_DIR / "cache"))
RUNS_DIR = Path(os.environ.get("CFBROOT_RUNS", PROJECT_DIR / "runs"))

load_dotenv(PROJECT_DIR / ".env")


def api_key() -> str:
    key = os.environ.get("CFBD_API_KEY", "").strip()
    if not key or key == "your_key_here":
        raise RuntimeError(
            "No CFBD API key found. Get a free one at https://collegefootballdata.com/key "
            "then put it in .env as CFBD_API_KEY=..."
        )
    return key


def has_api_key() -> bool:
    try:
        api_key()
        return True
    except RuntimeError:
        return False


@dataclass
class ModelParams:
    """Game outcome and committee ranking parameters.

    The game outcome values are fixed. Home field comes from
    ``python -m cfbroot.calibration``, which compares FPI to closing betting
    lines. Game noise and FPI's rating error come from
    ``python -m cfbroot.spread``, which compares ESPN's weekly FPI to how the
    rest of each season turned out. None are re-fit during a season.
    """

    # Game outcome model
    hfa: float = 2.75              # home field advantage, points
    # Game noise: what one game does beyond both teams' true strength. Holds
    # at 13.5 to 14 points all season in 2023-25.
    sigma: float = 13.86
    # A team's rating error: how far FPI is off about it, for the rest of the
    # season. Drawn once per simulated season, since a misjudged team is
    # misjudged every week. It shrinks as FPI sees results, from 7.2 points
    # before the season toward 4.7, closing 63% of the gap every 4.6 weeks:
    # about 6 after week 4 and 5 by mid-October. rating_sd is the value in use;
    # build_season() sets it from team_error() for the current week.
    rating_sd_start: float = 7.18
    rating_sd_floor: float = 4.68
    rating_sd_weeks: float = 4.60
    rating_sd: float = 7.18
    rating_scale: float = 1.0      # FPI is already in points; 0.97-1.07 measured
    fcs_rating: float = -32.0      # assumed rating for non-FBS opponents

    # Simulated scores. The margin is drawn first, from the same distribution
    # the win probability already came from, so no game's odds change; the
    # total is then drawn around it and the two scores fall out. Measured over
    # 2,398 FBS games in 2023-25: totals average 53.1 with an SD of 16.8, and
    # rise slightly with the margin (a blowout is a little higher scoring, not
    # lower), which is the slope below.
    total_base: float = 50.19
    total_slope: float = 0.180     # extra points per point of margin
    total_sd: float = 16.61

    # Source of the values above, for display
    calibration_n: int = 1496
    calibration_seasons: str = "2024-25"

    # Ranking and selection. The rating itself is Massey's, and its own
    # settings live in cfbroot.massey; these are the parts the Monte Carlo
    # needs to know about.
    #
    # Each simulated season solves the power rating until no rating moves by
    # more than fit_tol, which is far below anything that could change a rank.
    fit_tol: float = 1e-8
    fit_max_iter: int = 500
    # How far down the table the head-to-head pass looks. Only the top matters
    # to the field, and scanning all of it every season is wasted work.
    h2h_depth: int = 30
    committee_sd: float = COMMITTEE_SD
    title_jump_margin: float = TITLE_JUMP_MARGIN
    worst_loss_boost: float = WORST_LOSS_BOOST
    worst_loss_scale: float = WORST_LOSS_SCALE
    best_win_boost: float = BEST_WIN_BOOST
    best_win_scale: float = BEST_WIN_SCALE

    # Playoff size. Which teams get the automatic bids and the byes depends on
    # the season; see selection.playoff_format().
    playoff_size: int = 12
    n_byes: int = 4

    def team_error(self, weeks_played: int) -> float:
        """FPI's error about one team, after ``weeks_played`` weeks of games."""
        w = max(0, weeks_played)
        start2, floor2 = self.rating_sd_start ** 2, self.rating_sd_floor ** 2
        return float(math.sqrt(floor2 + (start2 - floor2)
                               * math.exp(-w / self.rating_sd_weeks)))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimConfig:
    """How many seasons to simulate. Model parameters live on SeasonState.params."""

    # The web app runs two and a half million seasons once at start-up,
    # because the result serves every team; the error on a playoff probability
    # is then about two and a half hundredths of a percentage point. Anything
    # calling the library directly gets a lighter default.
    n_sims: int = 100_000
    batch_size: int = 50_000
    seed: int = 12345

    def to_dict(self) -> dict:
        return asdict(self)


# Largest conference size the kernel's scratch arrays can hold.
MAX_CONF_SIZE = 64

# Conferences whose champion gets an automatic playoff bid.
POWER_CONFERENCES = {"ACC", "Big Ten", "Big 12", "SEC"}

# Conferences with no title game. All others play one between the top two
# finishers (or division winners).
NO_CCG_CONFERENCES: set[str] = set()

METRIC_NAMES = [
    "win_conference",
    "make_conf_title_game",
    "make_playoff",
    "top4_seed",
    "reach_quarterfinal",
    "reach_semifinal",
    "reach_title_game",
    "win_national_title",
    "undefeated_regular_season",
]

METRIC_LABELS = {
    "win_conference": "Win conference",
    "make_conf_title_game": "Reach conf title game",
    "make_playoff": "Make the playoff",
    "top4_seed": "Top-4 seed (first-round bye)",
    "reach_quarterfinal": "Reach quarterfinal",
    "reach_semifinal": "Reach semifinal",
    "reach_title_game": "Reach national title game",
    "win_national_title": "Win national title",
    "undefeated_regular_season": "Undefeated regular season",
}

# Metrics shown by default, in order.
DEFAULT_METRICS = [
    "make_playoff",
    "win_conference",
    "top4_seed",
    "win_national_title",
]
