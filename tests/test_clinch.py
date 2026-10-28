"""The title race logic against every way the games left could go.

Small conferences with a handful of games left can be played out in full, so
each claim the logic makes is checked against the outcomes themselves: a team
said to have clinched is in the top two in every one, a team said to be out
is behind two others in every one, and whenever neither is said, some
outcome proves it.
"""

import random
from itertools import product

from cfbroot.web.clinch import Race, _cover


def outcomes(race):
    """Every final win total ``race`` can end on, with the results that
    produce it."""
    left = [(a, b) for _, a, b in race.left]
    for bits in product((0, 1), repeat=len(left)):
        wins = dict(race.wins)
        for (a, b), bit in zip(left, bits):
            wins[a if bit == 0 else b] += 1
        yield wins


def ahead(race, wins, t, strict):
    """How many teams finish ahead of ``t``, or level with it unless strict."""
    mine = wins[t] / race.games[t]
    return sum(1 for i in race.teams if i != t and (
        wins[i] / race.games[i] > mine if strict else wins[i] / race.games[i] >= mine))


def random_race(rng, n=5, played=4, left=6):
    """A conference with some games played and some left, every team in at
    least one, as every real one is."""
    teams = list(range(n))
    pairs = [(a, b) for a in teams for b in teams if a < b]
    while True:
        done = []
        for _ in range(played):
            a, b = rng.choice(pairs)
            done.append((a, b) if rng.random() < 0.5 else (b, a))
        games = [(9 + k // 3, *rng.choice(pairs)) for k in range(left)]
        race = Race(set(teams), done, games)
        if all(race.games[t] for t in teams):
            return race


def test_clinched_and_out_match_every_outcome():
    rng = random.Random(7)
    seen = {"clinched": 0, "out": 0, None: 0}
    for _ in range(400):
        race = random_race(rng, n=rng.choice((4, 5, 6)), played=rng.randint(2, 8),
                           left=rng.randint(2, 7))
        every = list(outcomes(race))
        for t in race.teams:
            if not race.games[t]:
                continue
            truly_in = all(ahead(race, w, t, strict=False) <= 1 for w in every)
            truly_out = all(ahead(race, w, t, strict=True) >= 2 for w in every)
            said = race.status(t)
            seen[said] += 1
            assert (said == "clinched") == truly_in, (race.wins, race.left, t)
            assert (said == "out") == truly_out, (race.wins, race.left, t)
    assert seen["clinched"] and seen["out"] and seen[None]


def test_every_scenario_clinches_whatever_else_happens():
    rng = random.Random(11)
    checked = 0
    for _ in range(300):
        race = random_race(rng, n=5, played=rng.randint(3, 8), left=rng.randint(3, 7))
        week = race.left[0][0]
        for t in race.teams:
            if not race.games[t] or race.status(t):
                continue
            for way in race.this_week(t, week):
                # Fix the scenario's results, then play out everything else.
                fixed = {frozenset(g): g[0] for g in way}
                left = [(w, a, b) for w, a, b in race.left
                        if not (w == week and frozenset((a, b)) in fixed)]
                wins = dict(race.wins)
                for g in way:
                    wins[g[0]] += 1
                rest = Race(set(race.teams), [], left)
                rest.wins, rest.games = wins, race.games
                for w in outcomes(rest):
                    assert ahead(race, w, t, strict=False) <= 1, (race.wins, race.left, t, way)
                checked += 1
    assert checked


def test_scenarios_are_the_broadest_that_hold():
    # Two games, and a win in the first is enough on its own.
    hits = {(0, 0), (0, 1)}
    assert _cover(hits, 2) == [(0, None)]
    # Either game going the right way is enough.
    assert sorted(_cover({(0, 0), (0, 1), (1, 0)}, 2), key=str) == [(0, None), (None, 0)]
    assert _cover(set(), 3) == []
