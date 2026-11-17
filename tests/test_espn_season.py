"""ESPN answering a week with no games must not end the season early."""

import pytest

from cfbroot.data import espn_season as es


def _results(empty=()):
    return [((w, g), [] if (g == es.FBS and w in empty) or w > 15 else [{"id": w}])
            for g in (es.FBS, es.FCS) for w in es.WEEKS]


def test_an_empty_week_is_asked_for_again(monkeypatch):
    asked = []

    def events(year, week, group, *, live, force):
        asked.append((week, force))
        return [{"id": week}]

    monkeypatch.setattr(es, "_events", events)
    out = es._fill_gaps(2026, _results(empty={13, 14, 15}), live=False, pause=0)
    assert sorted(asked) == [(13, True), (14, True), (15, True)]
    fbs = {w for (w, g), ev in out if g == es.FBS and ev}
    assert fbs == set(range(1, 16))


def test_a_week_that_stays_empty_is_an_error(monkeypatch):
    monkeypatch.setattr(es, "_events", lambda *a, **k: [])
    with pytest.raises(es.ESPNSeasonError, match="week 13, 14"):
        es._fill_gaps(2026, _results(empty={13, 14}), live=False, pause=0)


def test_army_navy_week_may_be_empty(monkeypatch):
    monkeypatch.setattr(es, "_events", lambda *a, **k: [])
    out = es._fill_gaps(2026, _results(empty={15}), live=False, pause=0)
    assert {w for (w, g), ev in out if g == es.FBS and ev} == set(range(1, 15))


def test_weeks_after_the_season_may_be_empty(monkeypatch):
    def events(*a, **k):
        raise AssertionError("asked again for a week that has no games")

    monkeypatch.setattr(es, "_events", events)
    out = es._fill_gaps(2026, _results(), live=False, pause=0)
    assert out == _results()
