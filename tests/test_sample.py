"""A sample season: one simulated season kept in full."""

from cfbroot.sim.sample import sample_season


def test_a_sample_season_is_complete_and_consistent(midseason):
    s = sample_season(midseason, seed=3)
    real = {g["game_id"]: g for g in midseason.games if g["status"] != 0}

    assert len(s["games"]) == sum(1 for g in midseason.games if not g["is_ccg"])
    for g in s["games"]:
        assert g["home_points"] != g["away_points"]
        assert min(g["home_points"], g["away_points"]) >= 0
    played = [g for g in s["games"] if g["real"]]
    assert len(played) == len(real)

    field = [f["team"] for f in s["field"]]
    assert len(field) == 12 and len(set(field)) == 12
    assert [f["seed"] for f in s["field"]] == list(range(1, 13))
    assert [len(r["games"]) for r in s["rounds"]] == [4, 4, 2, 1]
    assert s["champion"] in field
    assert s["champion"] == s["rounds"][-1]["games"][0]["winner"]
    for tg in s["title_games"]:
        assert tg["winner"] in (tg["home"], tg["away"])


def test_the_same_seed_gives_the_same_season(midseason):
    a = sample_season(midseason, seed=11)
    b = sample_season(midseason, seed=11)
    assert a == b
    assert sample_season(midseason, seed=12)["games"] != a["games"]


def test_scores_are_ones_football_produces(midseason):
    from cfbroot.sim.sample import SCORE_FREQ
    s = sample_season(midseason, seed=5, from_start=True)
    for g in s["games"]:
        for pts in (g["home_points"], g["away_points"]):
            assert pts >= len(SCORE_FREQ) or SCORE_FREQ[pts] > 0, pts


def test_replaying_from_week_one_sets_the_real_results_aside(midseason):
    s = sample_season(midseason, seed=5, from_start=True)
    assert s["from_start"] and not any(g["real"] for g in s["games"])
    played = {(g["home_idx"], g["away_idx"]): (g["home_points"], g["away_points"])
              for g in midseason.games if g["status"] != 0}
    changed = sum((g["home_points"], g["away_points"]) != played[(g["home"], g["away"])]
                  for g in s["games"] if (g["home"], g["away"]) in played)
    assert changed > 0.9 * len(played)
