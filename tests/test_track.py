"""The private record of how the model does."""

import json
from datetime import datetime, timezone

from cfbroot.track import record, report


def site(tmp_path, upcoming, played):
    root = tmp_path / "site"
    (root / "data" / "team").mkdir(parents=True, exist_ok=True)
    state = {"year": 2026, "current_week": 7, "build": "b1",
             "team_index": {"0": {"name": "Alpha"}, "1": {"name": "Beta"},
                            "2": {"name": "Gamma"}, "3": {"name": "Delta"}},
             "upcoming": upcoming, "played": played}
    (root / "data" / "state.json").write_text(json.dumps(state), "utf-8")
    league = [{"team": "Alpha", "p": {"make_playoff": 0.6}},
              {"team": "Beta", "p": {"make_playoff": 0.1}}]
    (root / "data" / "team" / "alpha.json").write_text(json.dumps({"league": league}), "utf-8")
    return root


def test_keeps_the_last_chance_before_kickoff_then_the_score(tmp_path):
    into = tmp_path / "track"
    game = {"week": 7, "home": 0, "away": 1, "neutral": False,
            "start": "2026-10-17T19:30:00Z"}
    friday = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    saturday = datetime(2026, 10, 17, 18, tzinfo=timezone.utc)
    after = datetime(2026, 10, 17, 20, tzinfo=timezone.utc)

    record(site(tmp_path, [dict(game, p_home=0.70)], []), into, friday)
    record(site(tmp_path, [dict(game, p_home=0.74)], []), into, saturday)
    # A build after kickoff does not move the number the game is judged on.
    record(site(tmp_path, [dict(game, p_home=0.90)], []), into, after)
    played = [{"week": 7, "home": 0, "away": 1, "home_points": 31, "away_points": 10}]
    out = record(site(tmp_path, [], played), into, after)
    assert out["results"] == 1

    games = json.loads((into / "games-2026.json").read_text("utf-8"))
    g = games["7|Beta|Alpha"]
    assert g["first"]["p_home"] == 0.70
    assert g["last"]["p_home"] == 0.74
    assert g["score"] == [31, 10]
    # One odds snapshot a build, not one a call.
    assert len((into / "odds-2026.jsonl").read_text("utf-8").splitlines()) == 1

    text = report(into)
    assert "1 games played" in text and "Favourite won 1 of 1" in text
    assert "Brier score 0.0676" in text          # (0.74 - 1) ** 2


def test_report_before_anything_is_played(tmp_path):
    assert report(tmp_path) == "Nothing recorded yet."
