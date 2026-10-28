"""Who has a conference title game spot locked, who is out of the race, and
how a team still in it can lock a spot.

Only conference records count, as winning percentage, which is how every
conference orders its standings before the tiebreakers. A team has clinched
when, however the games left go, at most one other team can finish level
with it or ahead; a tie counts against it, since a tiebreaker might. A team
is out when, even winning every game left and every tie, two others must
finish ahead of it. Both are certain, so neither is claimed for a race only
the tiebreakers can settle.
"""

from __future__ import annotations

from collections import Counter, deque
from itertools import product

# A week with more deciding games than this is not broken into scenarios.
MAX_SCENARIO_GAMES = 10


def _games(state, conf):
    """The conference's teams, its finished games as (winner, loser), its
    games left as (week, home, away), and a title game already set."""
    members = {t.idx for t in state.fbs_teams if t.conference == conf}
    done, left, title = [], [], None
    for g in state.games:
        a, b = g["home_idx"], g["away_idx"]
        if a not in members or b not in members:
            continue
        if g.get("is_ccg"):
            title = (a, b)
            continue
        if not g.get("conference_game"):
            continue
        if g.get("completed"):
            done.append((a, b) if g["home_points"] > g["away_points"] else (b, a))
        else:
            left.append((g["week"], a, b))
    return members, done, left, title


def _place(k, free, owner, load, room):
    """Give game ``k`` a winner without any team passing its room, moving
    games already placed along a chain of teams if that makes space."""
    a, b = free[k]
    parent: dict = {a: None, b: None}
    queue = deque([a, b])
    while queue:
        u = queue.popleft()
        if load[u] < room[u]:
            load[u] += 1
            v = u
            while parent[v] is not None:
                x, moved = parent[v]
                owner[moved] = v
                v = x
            owner[k] = v
            return True
        for g2, o in list(owner.items()):
            if o != u:
                continue
            c, d = free[g2]
            y = d if c == u else c
            if y not in parent:
                parent[y] = (u, g2)
                queue.append(y)
    return False


class Race:
    """One conference's standings, and what its games left can still do."""

    def __init__(self, members, done, left, fixed=None):
        self.teams = sorted(members)
        self.fixed = fixed            # the title game, once it is set
        self.wins = {t: 0 for t in members}
        self.games = Counter()
        for w, l in done:
            self.wins[w] += 1
            self.games[w] += 1
            self.games[l] += 1
        for _, a, b in left:
            self.games[a] += 1
            self.games[b] += 1
        self.left = left

    def clinched(self, t, wins, left) -> bool:
        """Whether ``t`` is in however ``left`` goes: it loses every game it
        has left, and no two others can both finish level with it or ahead."""
        wins = dict(wins)
        free = []
        for a, b in left:
            if t in (a, b):
                wins[b if a == t else a] += 1
            else:
                free.append((a, b))
        wt, gt = wins[t], self.games[t]
        avail, pair = Counter(), Counter()
        for a, b in free:
            avail[a] += 1
            avail[b] += 1
            pair[frozenset((a, b))] += 1
        need = {}
        for i in self.teams:
            if i != t:
                # wins i needs to finish level or ahead: n / g_i >= wt / gt
                need[i] = max(0, -(-wt * self.games[i] // gt) - wins[i])
        rivals = [i for i in need if need[i] <= avail[i]]
        for x, i in enumerate(rivals):
            for j in rivals[x + 1:]:
                if need[i] + need[j] <= avail[i] + avail[j] - pair[frozenset((i, j))]:
                    return False
        return True

    def out(self, t, wins, left) -> bool:
        """Whether ``t`` is out however ``left`` goes: it wins every game it has
        left and every tie, and two others still finish ahead of it."""
        wins = dict(wins)
        free = []
        for a, b in left:
            if t in (a, b):
                wins[t] += 1
            else:
                free.append((a, b))
        wt, gt = wins[t], self.games[t]
        # the most wins each other team can have and not finish ahead
        cap = {i: wt * self.games[i] // gt - wins[i] for i in self.teams if i != t}
        ahead = [i for i in cap if cap[i] < 0]
        if len(ahead) >= 2:
            return True
        # Room for at most one team to finish ahead: if every game left can
        # be won without anyone else passing their cap, t is still alive.
        for loose in ([ahead] if ahead else [[]] + [[i] for i in cap]):
            room = {i: len(free) if i in loose else max(cap[i], 0) for i in cap}
            owner, load = {}, Counter()
            if all(_place(k, free, owner, load, room) for k in range(len(free))):
                return False
        return True

    def status(self, t) -> str | None:
        if self.fixed:
            return "clinched" if t in self.fixed else "out"
        left = [(a, b) for _, a, b in self.left]
        if not self.games[t]:
            return None
        if self.clinched(t, self.wins, left):
            return "clinched"
        if self.out(t, self.wins, left):
            return "out"
        return None

    def wins_out(self, t) -> bool:
        """Whether winning every game it has left clinches a spot for ``t``."""
        wins = dict(self.wins)
        left = []
        for _, a, b in self.left:
            if t in (a, b):
                wins[t] += 1
            else:
                left.append((a, b))
        return self.clinched(t, wins, left)

    def this_week(self, t, week) -> list[list[tuple]]:
        """The ways ``t`` clinches with this week's results, each a list of
        (winner, loser) games that does it whatever else happens."""
        rest = [(a, b) for w, a, b in self.left if w != week]
        now = [(a, b) for w, a, b in self.left if w == week]
        if not now:
            return []
        # Only games with t or a team that could still finish level with it
        # can change whether it is in; the rest are left to fall either way.
        wins = dict(self.wins)
        worst = wins[t]
        rivals = {i for i in self.teams if i != t and self.games[i]
                  and (wins[i] + sum(i in g for g in now + rest)) * self.games[t]
                  >= worst * self.games[i]}
        decide = [g for g in now if t in g or rivals & set(g)]
        other = [g for g in now if g not in decide]
        if len(decide) > MAX_SCENARIO_GAMES:
            return []
        hits = set()
        for bits in product((0, 1), repeat=len(decide)):
            w2 = dict(wins)
            for (a, b), bit in zip(decide, bits):
                w2[a if bit == 0 else b] += 1
            if self.clinched(t, w2, other + rest):
                hits.add(bits)
        return [[(g[0], g[1]) if bit == 0 else (g[1], g[0])
                 for g, bit in zip(decide, imp) if bit is not None]
                for imp in _cover(hits, len(decide))]


def _cover(hits, n):
    """The fewest broadest conditions that cover every outcome in ``hits``:
    each a tuple of 0, 1 or None, None meaning either result."""
    if not hits:
        return []
    terms = {tuple(h) for h in hits}
    primes = set()
    while terms:
        merged, used = set(), set()
        listed = sorted(terms, key=lambda x: tuple(-1 if v is None else v for v in x))
        for i, x in enumerate(listed):
            for y in listed[i + 1:]:
                diff = [k for k in range(n) if x[k] != y[k]]
                if len(diff) == 1 and x[diff[0]] is not None and y[diff[0]] is not None:
                    merged.add(x[:diff[0]] + (None,) + x[diff[0] + 1:])
                    used.update((x, y))
        primes |= terms - used
        terms = merged
    covered = lambda imp, h: all(v is None or v == h[k] for k, v in enumerate(imp))
    left, chosen = set(hits), []
    while left:
        best = max(primes, key=lambda p: (sum(covered(p, h) for h in left),
                                           -sum(v is not None for v in p)))
        chosen.append(best)
        left = {h for h in left if not covered(best, h)}
    return sorted(chosen, key=lambda p: sum(v is not None for v in p))


def conference_race(state, conf):
    """The title game race in ``conf``, or None where the top two by record
    is not how the game is filled."""
    from ..data.conference_rules import title_rules
    if title_rules(conf, state.year).divisions:
        return None
    members, done, left, title = _games(state, conf)
    if len(members) < 3:
        return None
    return Race(members, done, left, title)


def statuses(state) -> dict:
    """Every team that has clinched a title game spot or is out of the race,
    by index, for the team pages."""
    from .pages import conferences
    out = {"clinched": [], "out": []}
    for conf in conferences(state.fbs_teams):
        race = conference_race(state, conf)
        if race is None:
            continue
        for t in race.teams:
            s = race.status(t)
            if s:
                out[s].append(t)
    return out
