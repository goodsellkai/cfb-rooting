"""The title race logic against every way the games left could go.

Small conferences with a handful of games left can be played out in full, so
each claim the logic makes is checked against the outcomes themselves. How a
single finished season orders its teams is checked against the kernel, which
is the code the simulations run.
"""

import random
from itertools import product

import numpy as np

from cfbroot.config import MAX_CONF_SIZE
from cfbroot.data import conference_rules as CR
from cfbroot.sim import kernels as K
from cfbroot.web.clinch import Race, _cover, _separate, _threshold


def random_race(rng, n=5, played=4, left=6, rules=None, divisions=None):
    """A conference with some games played and some left, every team in at
    least one, as every real one is."""
    teams = list(range(n))
    pairs = [(a, b) for a in teams for b in teams if a < b]
    while True:
        done = []
        for _ in range(played):
            a, b = rng.choice(pairs)
            done.append((a, b) if rng.random() < 0.5 else (b, a))
        meetings = rng.sample(pairs, min(left, len(pairs)))
        games = [(9 + k // 3, a, b) for k, (a, b) in enumerate(meetings)]
        race = Race(set(teams), done, games, rules=rules, divisions=divisions)
        if all(race.games[t] for t in teams):
            return race


def play_out(race, fixed=None):
    """Every way the open games can finish, as the seats each one can fill."""
    fixed = fixed or {}
    for k, w in fixed.items():
        race._set(k, w)
    free = [k for k in range(len(race.slate)) if race.open[k]]
    for bits in product((0, 1), repeat=len(free)):
        chosen = race._results(free, bits)
        for k, w in chosen.items():
            race._set(k, w)
        yield chosen
        for k, w in chosen.items():
            race._unset(k, w)
    for k, w in fixed.items():
        race._unset(k, w)


def check_status(race, seen):
    for t in race.teams:
        i = race.ix[t]
        if not race.games[t] or not race.pools[i]:
            continue
        truly_in, truly_out = True, True
        for _ in play_out(race):
            seats = race._seats(i)
            truly_in = truly_in and all(i in s for s in seats)
            truly_out = truly_out and all(i not in s for s in seats)
        said = race.status(t)
        seen[said] += 1
        assert (said == "clinched") == truly_in, (race.wins, race.left, t)
        assert (said == "out") == truly_out, (race.wins, race.left, t)


def test_clinched_and_out_match_every_outcome():
    rng = random.Random(7)
    seen = {"clinched": 0, "out": 0, None: 0}
    for conf in ("SEC", "Big Ten", "Big 12", "Pac-12", "Mid-American"):
        rules = CR.title_rules(conf, 2026)
        for _ in range(60):
            check_status(random_race(rng, n=rng.choice((4, 5, 6)), played=rng.randint(2, 9),
                                     left=rng.randint(2, 7), rules=rules), seen)
    assert seen["clinched"] and seen["out"] and seen[None]


def test_a_tiebreak_it_holds_clinches_where_the_record_alone_would_not():
    # 0 is done at 3-1 and beat both 1 and 2, who can each still get to 3-1.
    # Three level on record is not enough by itself, but 0 beat the other two.
    done = [(0, 1), (0, 2), (0, 3), (4, 0), (1, 4), (2, 4), (3, 4), (1, 3), (2, 3),
            (3, 5), (4, 5)]
    race = Race(set(range(6)), done, [(9, 1, 5), (9, 2, 5)],
                rules=CR.title_rules("SEC", 2026))
    assert race._others_pass(0, race._level)
    assert race.status(0) == "clinched"
    # With the head-to-head the other way round, it is not safe.
    flipped = [(l, w) if {w, l} == {0, 1} else (w, l) for w, l in done]
    race = Race(set(range(6)), flipped, [(9, 1, 5), (9, 2, 5)],
                rules=CR.title_rules("SEC", 2026))
    assert race.status(0) != "clinched"


def eight_or_nine(rng, n=6):
    """Games among ``n`` teams with every team in eight or nine, the ACC's two
    schedule lengths in 2026."""
    pairs = [(a, b) for a in range(n) for b in range(n) if a < b]
    while True:
        games, count = [], [0] * n
        while min(count) < 8:
            a, b = rng.choice(pairs)
            if count[a] < 9 and count[b] < 9:
                games.append((a, b))
                count[a] += 1
                count[b] += 1
            elif all(count[x] >= 9 or count[y] >= 9 for x, y in pairs):
                break
        if min(count) >= 8 and len(set(count)) == 2:
            return games


def test_the_acc_counts_a_team_a_game_short_as_level():
    # 8-game teams against 9-game ones: a tie needs only wins or losses to
    # match, which percentages alone would miss.
    rng = random.Random(3)
    rules = CR.title_rules("ACC", 2026)
    seen = {"clinched": 0, "out": 0, None: 0}
    for _ in range(60):
        games = eight_or_nine(rng)
        rng.shuffle(games)
        n_left = rng.randint(2, 6)
        done = [(a, b) if rng.random() < 0.5 else (b, a) for a, b in games[n_left:]]
        left = [(10, a, b) for a, b in games[:n_left]]
        check_status(Race(set(range(6)), done, left, rules=rules), seen)
    assert seen["clinched"] or seen["out"]
    assert seen[None]


def test_a_division_sends_one_team():
    rng = random.Random(21)
    rules = CR.title_rules("Sun Belt", 2026)
    divisions = {0: "East", 1: "East", 2: "East", 3: "West", 4: "West", 5: "West"}
    seen = {"clinched": 0, "out": 0, None: 0}
    for _ in range(80):
        race = random_race(rng, n=6, played=rng.randint(4, 10), left=rng.randint(2, 6),
                           rules=rules, divisions=divisions)
        check_status(race, seen)
        assert race.spots == 1
    assert seen["clinched"] and seen["out"] and seen[None]


def kernel_pair(race, rng, divisions):
    """The title game the simulator's own code sends, with made-up ratings."""
    n = race.n
    members = np.arange(n, dtype=np.int32)
    results = [(w, l) for w in range(n) for l in range(n) for _ in range(race.res[w][l])]
    g_home = np.array([w for w, _ in results], dtype=np.int32)
    g_away = np.array([l for _, l in results], dtype=np.int32)
    winner = np.ones(len(results), dtype=np.uint8)
    cw = np.bincount(g_home, minlength=n).astype(np.int32)
    cl = np.bincount(g_away, minlength=n).astype(np.int32)
    score = np.array([rng.random() for _ in range(n)])
    names = sorted(set(race.div)) if divisions else []
    div_id = np.array([names.index(d) if divisions else -1 for d in race.div], dtype=np.int32)
    order = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
    pct = np.zeros(MAX_CONF_SIZE)
    mark = np.full(n, -1, dtype=np.int32)
    res = np.zeros((MAX_CONF_SIZE, MAX_CONF_SIZE), dtype=np.int32)
    rules = race.rules
    a, b = K._title_game_pair(
        members, n, cw, cl, cw.copy(), score, div_id, len(names),
        np.arange(len(results), dtype=np.int32), 0, len(results), g_home, g_away, winner,
        np.array(CR.step_row(rules.two), dtype=np.int8),
        np.array(CR.step_row(rules.multi), dtype=np.int8), rules.flags,
        order, pct, mark, res)
    return {int(a), int(b)}


def test_the_seats_are_the_ones_the_kernel_sends():
    rng = random.Random(5)
    divisions = {0: "East", 1: "East", 2: "East", 3: "West", 4: "West", 5: "West"}
    checked = 0
    for conf, divs in (("SEC", None), ("Big Ten", None), ("ACC", None), ("Pac-12", None),
                       ("Mountain West", None), ("Sun Belt", divisions)):
        rules = CR.title_rules(conf, 2026)
        for _ in range(250):
            race = random_race(rng, n=6, played=rng.randint(6, 14), left=0,
                               rules=rules, divisions=divs)
            if divs:
                options = [{x[0], y[0]} for x in race._seats(0) for y in race._seats(3)]
            else:
                options = [set(s) for s in race._seats(0)]
            for _ in range(6):
                assert kernel_pair(race, rng, divs) in options, (conf, race._done)
                checked += 1
    assert checked


def test_every_scenario_clinches_whatever_else_happens():
    rng = random.Random(11)
    checked = 0
    for _ in range(200):
        race = random_race(rng, n=5, played=rng.randint(3, 8), left=rng.randint(3, 7),
                           rules=CR.title_rules("SEC", 2026))
        week = race.left[0][0]
        for t in race.teams:
            if not race.games[t] or race.status(t):
                continue
            i = race.ix[t]
            for way in race.this_week(t, week):
                # Fix the scenario's results, then play out everything else.
                fixed, used = {}, set()
                for w, l in way:
                    k = next(k for k, g in enumerate(race.slate)
                             if {race.teams[g[0]], race.teams[g[1]]} == {w, l}
                             and race.week_of[k] == week and k not in used)
                    used.add(k)
                    fixed[k] = race.ix[w]
                for _ in play_out(race, fixed):
                    assert all(i in s for s in race._seats(i)), (race.wins, race.left, t, way)
                checked += 1
    assert checked


def test_each_row_of_a_path_says_what_the_outcomes_say():
    rng = random.Random(17)
    rows = 0
    for _ in range(120):
        race = random_race(rng, n=5, played=rng.randint(3, 8), left=rng.randint(3, 6),
                           rules=CR.title_rules("Big Ten", 2026))
        for t in race.teams:
            path = race.path(t, race.left[0][0])
            if path is None:
                continue
            i = race.ix[t]
            own = [k for k in race.of[i]]
            for row in path["rows"]:
                sets = [b for b in product((1, 0), repeat=len(own)) if sum(b) == row["wins"]]
                finishes = [race._results(own, b, i) for b in sets]
                always, ever = [], []
                for f in finishes:
                    seats = [race._seats(i) for _ in play_out(race, f)]
                    always.append(all(i in s for one in seats for s in one))
                    ever.append(any(i in s for one in seats for s in one))
                want = ("in" if all(always) else "some" if any(always)
                        else "help" if any(ever) else "out")
                assert row["state"] == want, (race.wins, race.left, t, row)
                rows += 1
    assert rows


def test_help_spelled_out_is_enough():
    rng = random.Random(29)
    checked = 0
    for _ in range(150):
        race = random_race(rng, n=5, played=rng.randint(4, 8), left=rng.randint(3, 6),
                           rules=CR.title_rules("SEC", 2026))
        for t in race.teams:
            path = race.path(t)
            if path is None:
                continue
            i = race.ix[t]
            own = list(race.of[i])
            for row in path["rows"]:
                for way in row.get("ways") or ():
                    fixed = {}
                    for w, l in way:
                        k = next(k for k, g in enumerate(race.slate)
                                 if {race.teams[g[0]], race.teams[g[1]]} == {w, l})
                        fixed[k] = race.ix[w]
                    for bits in product((1, 0), repeat=len(own)):
                        finish = race._results(own, bits, i)
                        if sum(bits) != row["wins"] or race._dead(i, finish):
                            continue
                        assert race._safe(i, {**finish, **fixed}), (race.wins, race.left, t, way)
                        checked += 1
    assert checked


def test_no_more_than_one_chaser_keeping_up_is_enough():
    """The sentence a row falls back on when the help is too long to list."""
    rng = random.Random(31)
    checked = 0
    for _ in range(120):
        race = random_race(rng, n=6, played=rng.randint(4, 9), left=rng.randint(3, 6),
                           rules=CR.title_rules("Big Ten", 2026))
        for t in race.teams:
            path = race.path(t)
            if path is None:
                continue
            i = race.ix[t]
            own = list(race.of[i])
            for row in path["rows"]:
                if row["state"] != "help":
                    continue
                final = race.w[i] + row["wins"]
                for bits in product((1, 0), repeat=len(own)):
                    if sum(bits) != row["wins"]:
                        continue
                    for _ in play_out(race, race._results(own, bits, i)):
                        rivals = [j for j in race.pools[i] if j != i
                                  and race._level(race.w[j], race.g[j], final, race.g[i])]
                        if len(rivals) <= 1:
                            assert all(i in s for s in race._seats(i)), (race._done, race.left, t)
                            checked += 1
    assert checked


def test_what_a_row_says_past_its_sure_thing():
    """A team can get in with more teams level than the sure thing allows, by
    winning the tiebreak, and the row has to say so only when that can happen."""
    rng = random.Random(37)
    said = {True: 0, False: 0}
    for conf in ("SEC", "Pac-12", "Mountain West"):
        for _ in range(80):
            race = random_race(rng, n=6, played=rng.randint(4, 10), left=rng.randint(3, 6),
                               rules=CR.title_rules(conf, 2026))
            for t in race.teams:
                path = race.path(t)
                if path is None:
                    continue
                i = race.ix[t]
                own = list(race.of[i])
                for row in path["rows"]:
                    if row["state"] != "help":
                        continue
                    final = race.w[i] + row["wins"]
                    found = False
                    for bits in product((1, 0), repeat=len(own)):
                        if sum(bits) != row["wins"]:
                            continue
                        finish = race._results(own, bits, i)
                        if race._dead(i, finish):
                            continue
                        for _ in play_out(race, finish):
                            seats = race._seats(i)
                            if row["ways"]:
                                hit = any(i in s for s in seats) and not all(i in s for s in seats)
                            else:
                                level = sum(race._level(race.w[j], race.g[j], final, race.g[i])
                                            for j in race.pools[i] if j != i)
                                hit = level >= race.spots and any(i in s for s in seats)
                            found = found or hit
                    assert row["beyond"] == found, (conf, race._done, race.left, t, row)
                    said[found] += 1
    assert said[True] and said[False]


def test_a_team_can_get_in_past_the_sure_thing():
    # 0 beat 1 and 2 and is done at 3-1. 1 and 2 each have a game left and can
    # join it at 3-1, and 3 is already 3-1 and 0 lost to no one in the tie but
    # never met 3, so with all of them level the tiebreak is what decides.
    done = [(0, 1), (0, 2), (0, 4), (5, 0), (3, 5), (3, 4), (3, 6), (1, 4), (2, 4), (1, 6), (2, 6)]
    race = Race(set(range(7)), done, [(9, 1, 5), (9, 2, 5)], rules=CR.title_rules("SEC", 2026))
    i = race.ix[0]
    assert race._beyond(i, race.w[i], [{}], False) is True


def test_each_tie_worked_out_matches_every_outcome():
    rng = random.Random(41)
    checked = 0
    for conf in ("SEC", "Big 12", "Pac-12", "Mountain West"):
        for _ in range(70):
            race = random_race(rng, n=6, played=rng.randint(5, 11), left=rng.randint(2, 6),
                               rules=CR.title_rules(conf, 2026))
            for t in race.teams:
                path = race.path(t)
                if path is None:
                    continue
                i = race.ix[t]
                own = list(race.of[i])
                for row in path["rows"]:
                    if not row.get("ties"):
                        continue
                    final = race.w[i] + row["wins"]
                    want, seen = {}, {}
                    for bits in product((1, 0), repeat=len(own)):
                        if sum(bits) != row["wins"]:
                            continue
                        finish = race._results(own, bits, i)
                        if row["state"] == "help" and race._dead(i, finish):
                            continue
                        for chosen in play_out(race, finish):
                            level = [j for j in race.pools[i] if j != i and race._level(
                                race.w[j], race.g[j], final, race.g[i])]
                            front = [j for j in level if race._ahead(race.w[j], race.g[j], final, race.g[i])]
                            if len(level) < race.spots or len(front) >= race.spots:
                                continue
                            seats = race._seats(i)
                            key = (tuple(race.teams[j] for j in front),
                                   tuple(race.teams[j] for j in level if j not in front))
                            verdict = ("in" if all(i in s for s in seats)
                                       else "out" if not any(i in s for s in seats) else "open")
                            want.setdefault(key, set()).add(verdict)
                            results = {**finish, **chosen}
                            games = set()
                            for k, w in results.items():
                                a, b = race.slate[k]
                                games.add((race.teams[w], race.teams[b if w == a else a]))
                            seen.setdefault(key, []).append((games, verdict))
                    assert {(tuple(e["ahead"]), tuple(e["level"])) for e in row["ties"]} == set(want), \
                        (conf, race._done, race.left, t, row)
                    for e in row["ties"]:
                        got = want[(tuple(e["ahead"]), tuple(e["level"]))]
                        if e["verdict"] == "depends":
                            assert len(got) > 1, (conf, race._done, race.left, t, e)
                            # What it names as enough is enough, and the rest is
                            # what it says the rest is.
                            for games, verdict in seen[(tuple(e["ahead"]), tuple(e["level"]))]:
                                named = any(set(way) <= games for way in e["ways"] or ())
                                if named:
                                    assert verdict == "in", (conf, race._done, race.left, t, e)
                                elif e["ways"] and e["else"] == "out":
                                    assert verdict == "out", (conf, race._done, race.left, t, e)
                        else:
                            assert got == {e["verdict"]}, (conf, race._done, race.left, t, e)
                        checked += 1
    assert checked


def test_a_tie_names_the_step_that_settles_it():
    rules = CR.title_rules("SEC", 2026)
    # 0 is done at 3-1 and beat 1 and 2, who can each still get to 3-1.
    done = [(0, 1), (0, 2), (0, 4), (5, 0), (3, 5), (3, 4), (1, 4), (2, 4), (1, 6), (2, 6), (3, 6)]
    race = Race(set(range(7)), done, [(9, 1, 5), (9, 2, 5)], rules=rules)
    i = race.ix[0]
    ties = race._ties(i, race.w[i], [{}], [race.ix[1], race.ix[2]])
    both = next(e for e in ties if set(e["level"]) == {1, 2})
    assert both["verdict"] == "in" and both["step"] == CR.H2H

    # 2 is ahead at 3-0. 0 and 1 are 2-1 and never met, so it goes to the
    # opponents they share, 3 and 4: 0 beat both, 1 split them.
    done = [(2, 0), (2, 4), (2, 5), (0, 3), (0, 4), (3, 1), (1, 4), (1, 5)]
    race = Race(set(range(6)), done, [], rules=rules)
    win = race._ties(race.ix[0], 2, [{}], [race.ix[1], race.ix[2]])[0]
    assert (win["ahead"], win["level"], win["verdict"], win["step"]) == ([2], [1], "in", CR.COMMON)
    assert win["shown"] == {0: "2-0", 1: "1-1"}
    lose = race._ties(race.ix[1], 2, [{}], [race.ix[0], race.ix[2]])[0]
    assert (lose["ahead"], lose["level"], lose["verdict"], lose["step"]) == ([2], [0], "out", CR.COMMON)

    # Where head-to-head is all the results can settle, the next step is open.
    race = Race(set(range(6)), done, [], rules=CR.title_rules("Mountain West", 2026))
    still = race._ties(race.ix[0], 2, [{}], [race.ix[1], race.ix[2]])[0]
    assert (still["verdict"], still["step"]) == ("open", CR.RATING)


def test_a_tie_is_told_by_the_result_it_turns_on():
    # A team has to lose exactly one of games 1, 2 and 3 to reach the tie, and
    # the tiebreak only goes its way when that loss is not game 1, so a win in
    # game 1 is all there is to say, though no outcome leaves the rest open.
    yes = [{1: "a", 2: "b", 3: "a"}, {1: "a", 2: "a", 3: "b"}]
    no = [{1: "b", 2: "a", 3: "a"}]
    assert _separate(yes, no, [1, 2, 3]) == [((1, "a"),)]
    # Two separate ways in.
    yes = [{1: "a", 2: "a"}, {1: "b", 2: "b"}]
    no = [{1: "a", 2: "b"}, {1: "b", 2: "a"}]
    assert _separate(yes, no, [1, 2]) == [((1, "a"), (2, "a")), ((1, "b"), (2, "b"))]
    # Nothing separates them, and it does not pretend to.
    assert _separate([{1: "a"}], [{1: "a"}], [1]) is None


def test_ties_are_not_listed_while_too_many_can_get_there():
    many = [(9 + k // 4, a, b) for k, (a, b) in enumerate(
        (a, b) for a in range(8) for b in range(8) if a < b)]
    race = Race(set(range(8)), [(0, 1)], many, rules=CR.title_rules("SEC", 2026))
    assert race._ties(0, 1, [{}], list(range(1, 8))) is None


def test_a_team_with_nothing_to_clinch_has_no_path():
    done = [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3), (2, 3)]
    race = Race(set(range(4)), done, [], rules=CR.title_rules("SEC", 2026))
    assert race.status(0) == "clinched" and race.path(0) is None
    assert race.status(3) == "out" and race.path(3) is None
    assert Race(set(range(4)), done, [], fixed=(0, 1)).status(2) == "out"


def test_scenarios_are_the_broadest_that_hold():
    # Two games, and a win in the first is enough on its own.
    hits = {(0, 0), (0, 1)}
    assert _cover(hits, 2) == [(0, None)]
    # Either game going the right way is enough.
    assert sorted(_cover({(0, 0), (0, 1), (1, 0)}, 2), key=str) == [(0, None), (None, 0)]
    assert _cover(set(), 3) == []


def test_a_number_of_wins_is_only_said_when_it_is_the_whole_story():
    # Any two of three.
    assert _threshold({(1, 1, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)}, 3) == 2
    # Two of three, but not those two.
    assert _threshold({(1, 1, 1), (1, 1, 0), (1, 0, 1)}, 3) is None
    assert _threshold(set(), 3) is None


def test_today_shows_how_a_tie_is_settled():
    # 0 is alone at 3-0. 1 and 2 are 2-1 and 1 beat 2, so 1 takes second.
    done = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3), (2, 4)]
    race = Race(set(range(5)), done, [], rules=CR.title_rules("SEC", 2026))
    now = race.today()[0]
    assert now["in"][0] == 0 and now["open"] == 0


def test_every_way_out_ends_the_race_whatever_else_happens():
    rng = random.Random(11)
    said = 0
    for _ in range(25):
        race = random_race(rng)
        week = min(race.week_of)
        now = [k for k in range(len(race.slate)) if race.week_of[k] == week]
        for t in race.teams:
            for way in race.out_this_week(t, week):
                said += 1
                fixed = {k: race.ix[w] for w, l in way for k in now
                         if set(race.slate[k]) == {race.ix[w], race.ix[l]}}
                assert len(fixed) == len(way)
                assert all(race.ix[t] not in s for _ in play_out(race, fixed)
                           for s in race._seats(race.ix[t]))
    assert said > 10


def test_short_conditions_hold_only_where_they_should():
    from cfbroot.web.clinch import _short_cover
    hits = {b for b in product((0, 1), repeat=4) if b[0] == 1 or (b[1] == 0 and b[2] == 1)}
    terms = _short_cover(hits, 4)
    assert (1, None, None, None) in terms and (None, 0, 1, None) in terms
    # nothing said twice, and nothing that reaches outside
    assert len(terms) == 2
    assert _short_cover(set(), 4) == []
    # a way that takes more results than are worth saying is left out
    assert _short_cover({(1, 1, 1, 1, 1)}, 5, most=4) == []
