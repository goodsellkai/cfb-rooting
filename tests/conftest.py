import numpy as np
import pytest

from cfbroot.config import ModelParams
from cfbroot.data.season import build_season
from cfbroot.data.synthetic import synthetic_season


@pytest.fixture(scope="session")
def midseason():
    """A fabricated season played through week 6."""
    return synthetic_season(seed=7, played_through=6)


@pytest.fixture(scope="session")
def preseason():
    return synthetic_season(seed=7, played_through=0)


def make_mini_season(games, teams=("A", "B", "C", "D"), ratings=None,
                     conference="Test Conf", params=None):
    """Build a one-conference season from ``(home, away, home_won)`` triples.

    ``home_won`` of None leaves the game unplayed.
    """
    ratings = ratings or {t: 0.0 for t in teams}
    teams_raw = [{"id": i + 1, "school": t, "abbreviation": t,
                  "conference": conference, "classification": "fbs"}
                 for i, t in enumerate(teams)]
    ids = {t["school"]: t["id"] for t in teams_raw}
    conferences_raw = [{"id": 1, "name": conference, "abbreviation": "TC",
                        "classification": "fbs", "member_count": len(teams)}]
    games_raw = []
    for n, (home, away, home_won) in enumerate(games):
        hp = ap = None
        if home_won is not None:
            hp, ap = (24, 17) if home_won else (17, 24)
        games_raw.append({
            "id": 1000 + n, "season": 2026, "week": 1 + n // 2,
            "season_type": "regular", "start_date": "2026-09-05T00:00:00Z",
            "completed": home_won is not None,
            "neutral_site": False, "conference_game": True,
            "home_id": ids[home], "home_team": home, "home_conference": conference,
            "home_classification": "fbs", "home_points": hp,
            "away_id": ids[away], "away_team": away, "away_conference": conference,
            "away_classification": "fbs", "away_points": ap,
            "notes": "",
        })
    fpi_raw = [{"team": t, "fpi": ratings[t]} for t in teams]
    return build_season(year=2026, teams_raw=teams_raw,
                        conferences_raw=conferences_raw, games_raw=games_raw,
                        fpi_raw=fpi_raw, params=params or ModelParams(),
                        recalibrate=False)
