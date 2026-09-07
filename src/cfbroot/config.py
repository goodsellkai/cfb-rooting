"""Configuration: paths, API credentials, and tunable model parameters."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from dotenv import load_dotenv

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
    """Parameters of the game-outcome and committee-ranking models.

    ``sigma`` and ``hfa`` are re-fit from completed games whenever there are
    enough of them (see :mod:`cfbroot.model`); the values here are the priors
    used in week 1 and as the shrinkage target later on.
    """

    # --- game outcome model ---
    hfa: float = 2.2               # home-field advantage, points
    sigma: float = 16.5            # SD of (actual margin - projected margin), points
    rating_scale: float = 1.0      # multiplier on the rating spread (calibration slope)
    fcs_rating: float = -32.0      # assumed rating for non-FBS opponents

    # --- committee ranking proxy ---
    # score = rating*w_rating + k_resume*(wins - elite_expected_wins) + k_champ*champion
    # Only the ratios matter -- the score is used for ranking alone -- so
    # w_rating is pinned at 1.0 and the others are expressed on the FPI scale.
    # These were tuned so that P(at-large bid | record) matches the 12-team
    # era: a 10-2 power-conference team is a strong favourite, 9-3 is a real
    # question, and 8-4 essentially never gets in without winning its league.
    w_rating: float = 1.0
    k_resume: float = 30.0         # committee credit per win above elite expectation
    k_champ: float = 5.0           # bonus for winning your conference
    elite_rating: float = 20.0     # the reference "playoff-caliber" team used for expected wins
    # How much of the schedule-strength adjustment to keep. 1.0 is a pure
    # strength-of-record term, which credits a brutal schedule so heavily that a
    # 4-loss elite team still outranks 10-win teams. 0.0 ignores schedule and
    # counts raw wins. The committee sits in between: it clearly weighs schedule,
    # but it has never taken a four-loss at-large team.
    resume_shrink: float = 0.30
    ccg_elite_expectation: float = 0.75  # elite team's expected wins in a conf title game

    # --- playoff structure (2026: 12 teams, straight seeding) ---
    playoff_size: int = 12
    n_auto_bids: int = 5           # 4 power champs + highest-ranked other champion
    n_byes: int = 4

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimConfig:
    """How much simulation to do. *What* to simulate lives on the SeasonState.

    Model parameters are deliberately not here: they are calibrated when the
    season is built and carried on ``SeasonState.params``, so there is exactly
    one place they can come from.
    """

    n_sims: int = 200_000
    batch_size: int = 20_000
    seed: int = 12345

    def to_dict(self) -> dict:
        return asdict(self)


# Upper bound on conference size; sizes the kernel's per-conference scratch.
MAX_CONF_SIZE = 64

# Conferences that receive a guaranteed playoff auto-bid for their champion.
POWER_CONFERENCES = {"ACC", "Big Ten", "Big 12", "SEC"}

# Conferences that do not stage a championship game. Everything else is assumed
# to play one between the top two finishers (or division winners).
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

# Metrics shown by default in the rooting guide, in priority order.
DEFAULT_METRICS = [
    "make_playoff",
    "win_conference",
    "top4_seed",
    "win_national_title",
]
