"""Paths, API key handling, and model parameters."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
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
    """Game outcome and committee ranking parameters.

    The game outcome values are fixed. They come from
    ``python -m cfbroot.calibration``, which compares FPI to closing betting
    lines. They are not re-fit during the season because FPI is updated after
    each week's games.
    """

    # Game outcome model
    hfa: float = 2.75              # home field advantage, points
    sigma: float = 15.26           # game noise: SD of results around a closing line
    # FPI's error against the market is 5.53 points on the gap between two
    # teams, so one team's own error is 5.53/sqrt(2). It is drawn once per
    # simulated season, since a misjudged rating is wrong all year.
    # sqrt(sigma^2 + 2*rating_sd^2) = 16.23, the calibrated total.
    rating_sd: float = 3.91
    rating_scale: float = 1.0      # FPI is already in points
    fcs_rating: float = -32.0      # assumed rating for non-FBS opponents

    # Source of the values above, for display
    calibration_n: int = 1496
    calibration_seasons: str = "2024-25"

    # Committee ranking proxy:
    #   score = w_rating*rating + k_resume*(wins - elite_expected_wins) + k_champ*champion
    # Tuned against ESPN's published playoff odds, subject to a 4-loss team
    # rarely getting an at-large bid. At shrink 0.30 the error was uneven by
    # conference (Big Ten +4.1pp, SEC -4.6pp) because hard schedules got too
    # little credit; 0.50 roughly halves that spread.
    w_rating: float = 1.0
    k_resume: float = 40.0         # credit per win above elite expectation
    k_champ: float = 5.0           # bonus for winning the conference
    elite_rating: float = 20.0     # rating of the reference playoff-level team
    resume_shrink: float = 0.50    # share of the schedule strength adjustment that is kept
    # Schedule strength from opponents' final records in that simulated season,
    # which is what makes a past opponent's later wins help you. Each game adds
    # (opponent win pct - 0.5), so a win over a team that finishes strong helps
    # and a loss to a team that collapses hurts most. At k_sos 8 the gap between
    # the toughest and softest schedule is worth roughly one win, which is about
    # what it has been worth to the committee.
    k_sos: float = 8.0
    sos_loss_ratio: float = 0.45   # a quality loss counts this much of a quality win
    ccg_elite_expectation: float = 0.75  # elite team's expected wins in a title game

    # Playoff format (2026: 12 teams, straight seeding)
    playoff_size: int = 12
    n_auto_bids: int = 5           # 4 power conference champions + best other champion
    n_byes: int = 4

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimConfig:
    """How many seasons to simulate. Model parameters live on SeasonState.params."""

    n_sims: int = 200_000
    batch_size: int = 20_000
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
