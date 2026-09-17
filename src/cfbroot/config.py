"""Paths, API key handling, and model parameters."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from .selection import COMMITTEE_SD, TITLE_JUMP_MARGIN

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
    # massey_iters is how many passes the per-season fit takes. The Hessian is
    # built at the curvature bound so every step is an under-step, which makes
    # the fit converge from anywhere, and each season starts from the last
    # one's answer. Eight passes and one correction match twelve and two to
    # within a hundredth of a place of rank and run a fifth faster.
    massey_iters: int = 8
    correction_passes: int = 1     # a second pass moves nothing measurable
    # How far down the table the head-to-head pass looks. Only the top matters
    # to the field, and scanning all of it every season is wasted work.
    h2h_depth: int = 30
    committee_sd: float = COMMITTEE_SD
    title_jump_margin: float = TITLE_JUMP_MARGIN

    # Playoff format, 2026-27: 12 teams, straight seeding, byes to the top four
    # seeds. Five automatic bids: the four power conference champions plus the
    # highest ranked Group of Six team, which from this season does not have to
    # have won its conference.
    playoff_size: int = 12
    n_auto_bids: int = 5
    n_byes: int = 4

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimConfig:
    """How many seasons to simulate. Model parameters live on SeasonState.params."""

    # The web app runs a million seasons once at start-up, because the result
    # serves every team; the error on a playoff probability is then about four
    # hundredths of a percentage point. Anything calling the library directly
    # gets a lighter default.
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
