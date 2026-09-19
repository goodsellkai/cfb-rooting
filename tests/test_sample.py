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
