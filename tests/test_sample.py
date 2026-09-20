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


def test_a_sample_season_carries_a_ranking_for_each_week(midseason):
    """The committee ranks every week, not only at the end."""
    from cfbroot.sim.sample import poll_weeks, weekly_systems

    weeks = poll_weeks(midseason)
    assert weeks, "the fixture season is too short to have any polls"
    s = sample_season(midseason, seed=4, weekly=weekly_systems(midseason))
    assert sorted(s["polls"], key=int) == [str(w) for w in weeks]

    fbs = {t.idx for t in midseason.fbs_teams}
    for order in s["polls"].values():
        assert len(order) == 25 and len(set(order)) == 25
        assert set(order) <= fbs

    # The last poll is close to the final ranking, but the title games and a
    # fresh draw of the committee's noise sit between them.
    last = s["polls"][str(weeks[-1])]
    final = [r["team"] for r in s["ranking"][:25]]
    assert len(set(last) & set(final)) >= 15


def test_polls_are_left_out_when_no_weekly_systems_are_given(midseason):
    assert sample_season(midseason, seed=4)["polls"] == {}
