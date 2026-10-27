"""The committee's ranking on the results so far, and the field it gives."""

from cfbroot.config import POWER_CONFERENCES
from cfbroot.sim.sample import committee_now


def test_the_projection_ranks_every_team_once(midseason):
    proj = committee_now(midseason)
    assert proj["week"] == midseason.current_week() - 1
    fbs = {t.idx for t in midseason.fbs_teams}
    assert sorted(proj["order"]) == sorted(fbs)


def test_the_field_if_it_ended_today_follows_the_rules(midseason):
    proj = committee_now(midseason)
    field = proj["field"]
    assert [f["seed"] for f in field] == list(range(1, 13))
    assert len({f["team"] for f in field}) == 12
    assert [f["bye"] for f in field] == [True] * 4 + [False] * 8
    teams = {f["team"] for f in field}
    conf = {t.idx: t.conference for t in midseason.fbs_teams}
    # every power conference leader is in, and the next four out are not
    for c, idx in proj["leaders"].items():
        if c in POWER_CONFERENCES:
            assert idx in teams, c
    assert not set(proj["out"]) & teams
    assert all(conf[i] for i in proj["leaders"].values())


def test_the_projection_is_one_fixed_answer(midseason):
    assert committee_now(midseason) == committee_now(midseason)


def test_no_projection_before_any_week_is_played(preseason):
    assert committee_now(preseason) is None
