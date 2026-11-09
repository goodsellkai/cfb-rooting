"""Who has a conference title game spot locked, who is out of the race, and
what each team still in it needs.

The games left are played out against each conference's own tiebreakers, the
same steps the kernel runs (`data/conference_rules.py`). A team has clinched
when it is in however they go, and is out when it is in under none of them.
Only the results count: a step they cannot settle, the rating at the end of
every list or the Big 12's total wins, could go any way. So a team is never
called in on it, nor out because of it.

Most of a conference's games stop mattering quickly, so the search asks the
records first and only plays out the games a tie could turn on. One that runs
past `MAX_NODES` answers the cautious way, with neither claim made.
"""

from __future__ import annotations

from collections import Counter, deque
from itertools import combinations, product

from ..data import conference_rules as CR

# More deciding games than this and a week is not broken into scenarios.
MAX_SCENARIO_GAMES = 10
# The same for the results a team needs from other games, over all that are left.
MAX_HELP_GAMES = 8
# The most a scenario can ask before a list of results stops being readable.
MAX_WAYS = 3
MAX_WAY_RESULTS = 4
MAX_NODES = 1_000
# Ties are worked out by name once this few teams can catch a finish or this few
# games are left in the conference, and the ways they can tie fit in a short list.
MAX_TIE_TEAMS = 5
MAX_TIE_OPEN = 10
MAX_TIE_LINES = 24
MAX_TIE_NODES = 3_000
# The most a tie's own conditions can ask before "depends on other results" is
# the clearer thing to say.
MAX_TIE_WAYS = 3
MAX_TIE_RESULTS = 3


class _Need(Exception):
    """A tie cannot be settled until this team's games are. ``code`` is the
    step that asked, where one did."""

    def __init__(self, team, code=None):
        self.team = team
        self.code = code


class _TooMany(Exception):
    """More ways to tie than are worth listing."""


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


def _rate(w, l):
    return w / (w + l) if w + l > 0 else 0.0


class Race:
    """One conference's standings, and what its games left can still do.

    Teams are passed in and out by their index in the season. Inside, they are
    numbered from zero so a results table can be a plain list.
    """

    def __init__(self, members, done, left, fixed=None, rules=None, divisions=None,
                 overall=None):
        self.teams = sorted(members)
        self.fixed = fixed            # the title game, once it is set
        self.rules = rules or CR.title_rules(None, 0)
        self.left = left
        self.overall = overall or {}  # every win, for a tie shown as it stands
        self._done, self._divisions = done, divisions
        self.wins = {t: 0 for t in members}
        self.games = Counter()
        for w, l in done:
            self.wins[w] += 1
            self.games[w] += 1
            self.games[l] += 1
        for _, a, b in left:
            self.games[a] += 1
            self.games[b] += 1

        self.ix = {t: i for i, t in enumerate(self.teams)}
        n = self.n = len(self.teams)
        self.g = [self.games[t] for t in self.teams]
        self.w = [self.wins[t] for t in self.teams]
        self.res = [[0] * n for _ in range(n)]
        for w, l in done:
            self.res[self.ix[w]][self.ix[l]] += 1
        self.slate = [(self.ix[a], self.ix[b]) for _, a, b in left]
        self.week_of = [w for w, _, _ in left]
        self.open = [True] * len(left)
        self.free = [0] * n
        self.of = [[] for _ in range(n)]
        for k, (a, b) in enumerate(self.slate):
            self.free[a] += 1
            self.free[b] += 1
            self.of[a].append(k)
            self.of[b].append(k)

        self.unequal = bool(self.rules.flags & CR.UNEQUAL)
        self.restart = bool(self.rules.flags & CR.RESTART)
        self.spots = 1 if divisions else 2
        self.div = [(divisions or {}).get(t) for t in self.teams]
        self.pools = [
            [j for j in range(n) if self.div[j] == self.div[i]]
            if divisions and self.div[i] else
            ([] if divisions else list(range(n)))
            for i in range(n)]
        self._memo: dict = {}
        self._paths: dict = {}
        self._budget = 0
        self._totals_settled = False  # total wins, once no game can change them

    # The results, with the games left set and unset as the search goes.

    def _set(self, k, winner):
        a, b = self.slate[k]
        loser = b if winner == a else a
        self.w[winner] += 1
        self.res[winner][loser] += 1
        self.free[a] -= 1
        self.free[b] -= 1
        self.open[k] = False

    def _unset(self, k, winner):
        a, b = self.slate[k]
        loser = b if winner == a else a
        self.w[winner] -= 1
        self.res[winner][loser] -= 1
        self.free[a] += 1
        self.free[b] += 1
        self.open[k] = True

    # Where one team stands against another. In the ACC a team a game short of
    # another is level with it on the same wins or the same losses, whatever
    # the percentages say.

    def _joined(self, n, gi, wt, gt):
        return (self.unequal and abs(gi - gt) == 1
                and (n == wt or gi - n == gt - wt))

    def _level(self, n, gi, wt, gt):
        return n * gt >= wt * gi or self._joined(n, gi, wt, gt)

    def _ahead(self, n, gi, wt, gt):
        return n * gt > wt * gi and not self._joined(n, gi, wt, gt)

    def _others_pass(self, i, test) -> bool:
        """With ``i`` losing all it has left, whether enough others can pass
        ``test`` against it to fill the spots."""
        wins = self.w[:]
        avail, pair = [0] * self.n, Counter()
        for k, (a, b) in enumerate(self.slate):
            if not self.open[k]:
                continue
            if a == i:
                wins[b] += 1
            elif b == i:
                wins[a] += 1
            else:
                avail[a] += 1
                avail[b] += 1
                pair[(a, b) if a < b else (b, a)] += 1
        wt, gt = wins[i], self.g[i]
        need = []
        for j in self.pools[i]:
            if j == i:
                continue
            for x in range(avail[j] + 1):
                if test(wins[j] + x, self.g[j], wt, gt):
                    need.append((j, x))
                    break
        if self.spots == 1:
            return bool(need)
        for y, (a, na) in enumerate(need):
            for b, nb in need[y + 1:]:
                p = pair[(a, b) if a < b else (b, a)]
                if na + nb <= avail[a] + avail[b] - p:
                    return True
        return False

    def _room_under(self, i, test) -> bool:
        """With ``i`` winning all it has left, whether the rest can go so that
        too few others pass ``test`` against it to fill the spots."""
        wins = self.w[:]
        free = []
        for k, (a, b) in enumerate(self.slate):
            if not self.open[k]:
                continue
            if i in (a, b):
                wins[i] += 1
            else:
                free.append((a, b))
        wt, gt = wins[i], self.g[i]
        pool = set(self.pools[i])
        cap = {}
        for j in range(self.n):
            if j == i:
                continue
            if j not in pool:
                cap[j] = len(free)      # the other division does not matter
                continue
            most = -1
            for x in range(len(free) + 1):
                if test(wins[j] + x, self.g[j], wt, gt):
                    break
                most = x
            cap[j] = most
        over = [j for j in cap if cap[j] < 0]
        if len(over) >= self.spots:
            return False
        if self.spots - 1 > len(over):
            choices = [[]] + [[j] for j in pool if j != i]
        else:
            choices = [over]
        for loose in choices:
            room = {j: len(free) if j in loose else max(cap[j], 0) for j in cap}
            owner, load = {}, Counter()
            if all(_place(k, free, owner, load, room) for k in range(len(free))):
                return True
        return False

    # A tie, step by step: the same walk as kernels._break_tie.

    def _pick(self, grp, div, k=0, log=None) -> set:
        """Who can come out of a tied group: one team, unless a step the
        results cannot settle decides it. ``log`` collects the steps."""
        grp = list(grp)
        res = self.res
        while len(grp) > 1:
            n = len(grp)
            steps = self.rules.two if n == 2 else self.rules.multi
            code = steps[k] if k < len(steps) else CR.RATING
            if code == CR.END:
                code = CR.RATING
            for a in grp:
                if self.free[a]:
                    raise _Need(a, code)
            if code == CR.RATING:
                if log is not None:
                    log.append({"code": code, "teams": grp[:], "keep": grp[:]})
                return set(grp)
            if code == CR.TOTAL_WINS and not self._totals_settled:
                if log is not None:
                    log.append({"code": code, "teams": grp[:], "keep": grp[:]})
                out = set()
                for size in range(1, n + 1):
                    for sub in combinations(grp, size):
                        again = size < n and (size == 2 or self.restart)
                        out |= self._pick(sub, div, 0 if again else k + 1)
                return out

            note = None
            if code == CR.H2H:
                met = all(res[a][b] + res[b][a] for a, b in combinations(grp, 2))
                if met:
                    pairs = [(sum(res[a][b] for b in grp), sum(res[b][a] for b in grp))
                             for a in grp]
                else:
                    beat = next((a for a in grp if all(
                        b == a or (res[a][b] > 0 and res[b][a] == 0) for b in grp)), None)
                    if beat is not None:
                        if log is not None:
                            log.append({"code": code, "teams": grp[:], "keep": [beat],
                                        "note": "beat"})
                        return {beat}
                    lost = next((a for a in grp if all(
                        b == a or (res[b][a] > 0 and res[a][b] == 0) for b in grp)), None)
                    if lost is not None:
                        if log is not None:
                            log.append({"code": code, "teams": grp[:], "dropped": lost,
                                        "keep": [a for a in grp if a != lost],
                                        "note": "lost"})
                        grp.remove(lost)
                        k = 0
                        continue
                    pairs, note = [(0, 0)] * n, "unmet"
                vals = [_rate(*p) for p in pairs]
                shown = [f"{w}-{l}" for w, l in pairs]
            elif code in (CR.COMMON, CR.NEXT):
                common = []
                for o in range(self.n):
                    if o in grp:
                        continue
                    if div is not None:
                        same = self.div[o] == div
                        if (code == CR.COMMON and same) or (code == CR.NEXT and not same):
                            continue
                    if all(res[a][o] + res[o][a] for a in grp):
                        common.append(o)
                if code == CR.COMMON:
                    pairs = [(sum(res[a][o] for o in common), sum(res[o][a] for o in common))
                             for a in grp]
                else:
                    # Down the standings a level at a time, teams level on
                    # percentage taken together.
                    pairs, note = [(0, 0)] * n, None
                    for o in common:
                        if self.free[o]:
                            raise _Need(o, code)
                    pct = {o: self.w[o] / max(self.g[o], 1) for o in common}
                    done = set()
                    for level in sorted(set(pct.values()), reverse=True):
                        rung = [o for o in common if abs(pct[o] - level) <= 1e-12]
                        if rung[0] in done:
                            continue
                        done.update(rung)
                        tried = [(sum(res[a][o] for o in rung), sum(res[o][a] for o in rung))
                                 for a in grp]
                        v = [_rate(*p) for p in tried]
                        if any(abs(x - v[0]) > 1e-12 for x in v):
                            pairs, note = tried, rung
                            break
                vals = [_rate(*p) for p in pairs]
                shown = [f"{w}-{l}" for w, l in pairs]
            elif code == CR.OPP_PCT:
                vals = []
                for a in grp:
                    tot, cnt = 0.0, 0
                    for o in range(self.n):
                        gp = res[a][o] + res[o][a]
                        if gp:
                            if self.free[o]:
                                raise _Need(o, code)
                            tot += gp * self.w[o] / max(self.g[o], 1)
                            cnt += gp
                    vals.append(tot / cnt if cnt else 0.0)
                shown = [f"{v:.3f}".lstrip("0") for v in vals]
            elif code == CR.TOTAL_WINS:
                vals = [float(self.overall.get(self.teams[a], 0)) for a in grp]
                shown = [str(int(v)) for v in vals]
            else:                       # the division's own record
                pairs = []
                for a in grp:
                    mates = [o for o in range(self.n) if o != a and self.div[o] == self.div[a]]
                    pairs.append((sum(res[a][o] for o in mates), sum(res[o][a] for o in mates)))
                vals = [_rate(*p) for p in pairs]
                shown = [f"{w}-{l}" for w, l in pairs]

            top = max(vals)
            keep = [a for a, v in zip(grp, vals) if v >= top - 1e-12]
            if log is not None:
                log.append({"code": code, "teams": grp[:], "shown": dict(zip(grp, shown)),
                            "keep": keep[:], "note": note})
            if len(keep) == n:
                k += 1
                continue
            grp = keep
            if len(grp) > 1:
                k = 0 if len(grp) == 2 or self.restart else k + 1
        return {grp[0]}

    def _tied(self, pool, taken) -> list:
        """The teams level at the top of what is not yet taken. A team whose
        games are not all settled and could still be among them stops it."""
        rest = [j for j in pool if j not in taken]
        if not rest:
            return []
        best = max(self.w[j] / max(self.g[j], 1) for j in rest)
        for j in rest:
            if self.free[j] and (self.w[j] + self.free[j]) / max(self.g[j], 1) >= best - 1e-12:
                raise _Need(j)
        grp = [j for j in rest if abs(self.w[j] / max(self.g[j], 1) - best) <= 1e-12]
        if self.unequal:
            leaders = grp[:]
            for j in rest:
                if j in leaders:
                    continue
                wins = range(self.w[j], self.w[j] + self.free[j] + 1)
                joins = any(self._joined(x, self.g[j], self.w[m], self.g[m])
                            for x in wins for m in leaders)
                if joins and self.free[j]:
                    raise _Need(j)
                if joins:
                    grp.append(j)
        return grp

    def _seats(self, i) -> list:
        """Every way the spots ``i`` is racing for can fill."""
        pool = self.pools[i]
        div = self.div[i]
        out = []

        def fill(taken):
            if len(taken) == self.spots:
                out.append(taken)
                return
            grp = self._tied(pool, taken)
            if not grp:
                out.append(taken)
                return
            for p in self._pick(grp, div):
                fill(taken + (p,))

        fill(())
        return out

    def _verdict(self, i) -> str:
        """With the results settled enough to tell: "in", "out", or "open" if
        a step the results cannot settle decides it."""
        pool, div = self.pools[i], self.div[i]

        def fill(taken):
            if i in taken:
                return {"in"}
            if len(taken) == self.spots:
                return {"out"}
            grp = self._tied(pool, taken)
            if not grp:
                return {"out"}
            out = set()
            for p in self._pick(grp, div):
                out |= fill(taken + (p,))
            return out

        got = fill(())
        return got.pop() if len(got) == 1 else "open"

    def _deciding_step(self, i):
        """The step that settles ``i``'s place, and what it showed: the one
        that took it out of a tie, or the last one it was in."""
        pool, div = self.pools[i], self.div[i]
        taken, code, shown = (), None, None
        while len(taken) < self.spots and i not in taken:
            grp = self._tied(pool, taken)
            if not grp:
                break
            if len(grp) <= self.spots - len(taken):
                taken += tuple(grp)
                continue
            log = []
            got = self._pick(grp, div, log=log)
            if i in grp:
                for step in log:
                    code, shown = step["code"], step.get("shown")
                    if i not in step["keep"]:
                        break
            if len(got) > 1:
                code = log[-1]["code"] if log else code
                shown = None
                break
            taken += tuple(got)
        return code, shown

    # The search.

    def _next_game(self, i):
        """The open game most likely to matter to ``i``, and the team in it
        that it matters through: its own games, then those of any team that
        could still reach its record."""
        for k in self.of[i]:
            if self.open[k]:
                return k, i
        wt, gt = self.w[i], self.g[i]
        for j in self.pools[i]:
            if j != i and self.free[j] and self._level(self.w[j] + self.free[j], self.g[j], wt, gt):
                for k in self.of[j]:
                    if self.open[k]:
                        return k, j
        return None, None

    def _other(self, k, side):
        a, b = self.slate[k]
        return b if a == side else a

    def _can_miss(self, i) -> bool:
        """Whether the games left can go so that ``i`` might not be in."""
        self._budget -= 1
        if self._budget < 0:
            return True
        if not self._others_pass(i, self._level):
            return False
        if self._others_pass(i, self._ahead):
            return True
        k, side = self._next_game(i)
        if k is None:
            try:
                return any(i not in s for s in self._seats(i))
            except _Need as need:
                k = next(g for g in self.of[need.team] if self.open[g])
                side = need.team
        # The bad result for i first, since one way to miss ends the search.
        worse = self._other(k, i) if side == i else side
        for winner in (worse, self._other(k, worse)):
            self._set(k, winner)
            hit = self._can_miss(i)
            self._unset(k, winner)
            if hit:
                return True
        return False

    def _can_make(self, i) -> bool:
        """Whether the games left can go so that ``i`` might be in."""
        self._budget -= 1
        if self._budget < 0:
            return True
        if not self._room_under(i, self._ahead):
            return False
        if self._room_under(i, self._level):
            return True
        k, side = self._next_game(i)
        if k is None:
            try:
                return any(i in s for s in self._seats(i))
            except _Need as need:
                k = next(g for g in self.of[need.team] if self.open[g])
                side = need.team
        better = i if side == i else self._other(k, side)
        for winner in (better, self._other(k, better)):
            self._set(k, winner)
            hit = self._can_make(i)
            self._unset(k, winner)
            if hit:
                return True
        return False

    def _ask(self, kind, i, fixed):
        key = (kind, i, tuple(sorted(fixed.items())))
        if key not in self._memo:
            for k, w in fixed.items():
                self._set(k, w)
            self._budget = MAX_NODES
            self._memo[key] = (self._can_miss if kind == "miss" else self._can_make)(i)
            for k, w in fixed.items():
                self._unset(k, w)
        return self._memo[key]

    def _safe(self, i, fixed=None) -> bool:
        return not self._ask("miss", i, fixed or {})

    def _dead(self, i, fixed=None) -> bool:
        return not self._ask("make", i, fixed or {})

    # What the pages ask.

    def status(self, t) -> str | None:
        if self.fixed:
            return "clinched" if t in self.fixed else "out"
        i = self.ix[t]
        if not self.games[t] or not self.pools[i]:
            return None
        if self._safe(i):
            return "clinched"
        if self._dead(i):
            return "out"
        # A question too big to settle at once can come apart into the team's
        # own finishes, each with less left to search.
        own = [k for k in self.of[i] if self.open[k]]
        finishes = [self._results(own, bits, i) for bits in product((1, 0), repeat=len(own))]
        if all(self._safe(i, f) for f in finishes):
            return "clinched"
        if all(self._dead(i, f) for f in finishes):
            return "out"
        return None

    def _results(self, ks, bits, i=None):
        """Games as results: for each of ``ks``, bit 1 is ``i`` winning, or
        with no ``i`` the first-listed team."""
        out = {}
        for k, bit in zip(ks, bits):
            a, b = self.slate[k]
            side = a if i is None else i
            out[k] = side if bit else self._other(k, side)
        return out

    def wins_out(self, t) -> bool:
        """Whether winning every game it has left clinches a spot for ``t``."""
        i = self.ix[t]
        return self._safe(i, {k: i for k in self.of[i]})

    def _deciding(self, i, ks) -> list:
        """Those of games ``ks`` that can change whether ``i`` is in: its own,
        and those of any team that could finish level with it at its worst."""
        wt, gt = self.w[i], self.g[i]
        rivals = {j for j in self.pools[i] if j != i and self.g[j]
                  and self._level(self.w[j] + self.free[j], self.g[j], wt, gt)}
        return [k for k in ks if i in self.slate[k] or rivals & set(self.slate[k])]

    def _way(self, ks, term):
        """A condition from `_cover` as (winner, loser) games, by team index."""
        out = []
        for k, bit in zip(ks, term):
            if bit is not None:
                a, b = self.slate[k]
                w, l = (a, b) if bit else (b, a)
                out.append((self.teams[w], self.teams[l]))
        return out

    def this_week(self, t, week) -> list[list[tuple]]:
        """The ways ``t`` clinches with this week's results, each a list of
        (winner, loser) games that does it whatever else happens."""
        i = self.ix[t]
        now = [k for k in range(len(self.slate)) if self.week_of[k] == week]
        decide = self._deciding(i, now)
        if not decide or len(decide) > MAX_SCENARIO_GAMES:
            return []
        hits = {bits for bits in product((0, 1), repeat=len(decide))
                if self._safe(i, self._results(decide, bits))}
        return [self._way(decide, term) for term in _cover(hits, len(decide))]

    def _chasers(self, i, final, own_sets):
        """Who can finish level with ``final`` wins or better in any of these
        finishes for ``i``, and who already has."""
        gt = self.g[i]
        chasing, already = [], []
        for j in self.pools[i]:
            if j == i or not self.g[j]:
                continue
            if self._level(self.w[j], self.g[j], final, gt):
                already.append(j)
            if any(self._level(self.w[j] + self.free[j]
                               - sum(1 for k, w in own.items()
                                     if w == i and j in self.slate[k]),
                               self.g[j], final, gt) for own in own_sets):
                chasing.append(j)
        return chasing, already

    def _help(self, i, own_sets):
        """The results from other games that put ``i`` in with every one of
        these finishes, where few enough games are in play to spell them out."""
        taken = set(own_sets[0])
        rest = [k for k in range(len(self.slate)) if k not in taken]
        final = self.w[i] + sum(1 for w in own_sets[0].values() if w == i)
        chasing, _ = self._chasers(i, final, own_sets)
        ks = [k for k in rest if set(chasing) & set(self.slate[k])]
        if not ks or len(ks) > MAX_HELP_GAMES:
            return None
        hits = set()
        for bits in product((0, 1), repeat=len(ks)):
            others = self._results(ks, bits)
            if all(self._safe(i, {**own, **others}) for own in own_sets):
                hits.add(bits)
        if not hits:
            return None
        ways = [self._way(ks, term) for term in _cover(hits, len(ks))]
        if len(ways) > MAX_WAYS or any(len(w) > MAX_WAY_RESULTS for w in ways):
            return None
        return ways

    def _find(self, i, test, crowded) -> bool:
        """Whether the open games can go so that ``test`` holds of the seats.
        ``crowded`` asks for too many others level with ``i`` to be sure of it,
        which stops the search once too few can get there."""
        self._budget -= 1
        if self._budget < 0:
            return False
        wt, gt = self.w[i], self.g[i]
        ahead = can = 0
        for j in self.pools[i]:
            if j != i:
                ahead += self._ahead(self.w[j], self.g[j], wt, gt)
                can += self._level(self.w[j] + self.free[j], self.g[j], wt, gt)
        if ahead >= self.spots or (crowded and can < self.spots):
            return False
        k, side = self._next_game(i)
        if k is None:
            try:
                return test(self._seats(i))
            except _Need as need:
                k = next(g for g in self.of[need.team] if self.open[g])
                side = need.team
        better = i if side == i else self._other(k, side)
        for winner in (better, self._other(k, better)):
            self._set(k, winner)
            hit = self._find(i, test, crowded)
            self._unset(k, winner)
            if hit:
                return True
        return False

    def _beyond(self, i, final, finishes, spelled):
        """Whether a finish can still put ``i`` in past the sure thing a row
        names. With no results spelled out, that is more teams level with it
        than the sure thing allows and a tiebreak going its way. With results
        spelled out, those are every way the tiebreaks settle it, so what is
        left is a tie reaching a step they cannot settle. True, False, or None
        if the search ran out first."""
        gt = self.g[i]

        def test(seats):
            if not any(i in s for s in seats):
                return False
            if spelled:
                return not all(i in s for s in seats)
            return (sum(self._level(self.w[j], self.g[j], final, gt)
                        for j in self.pools[i] if j != i) >= self.spots)

        self._budget = MAX_NODES
        for finish in finishes:
            for k, w in finish.items():
                self._set(k, w)
            hit = self._find(i, test, not spelled)
            for k, w in finish.items():
                self._unset(k, w)
            if hit:
                return True
        return None if self._budget < 0 else False

    def _ties(self, i, final, finishes, chasing):
        """Every way too many others can finish with ``i`` to be sure of it, and
        how that comes out: which teams are ahead and level, whether ``i`` is
        in or out, and on what step. None where too many teams can get there or
        there are too many ways to list."""
        if not chasing or (len(chasing) > MAX_TIE_TEAMS and sum(self.open) > MAX_TIE_OPEN):
            return None
        gt = self.g[i]
        pool = [j for j in self.pools[i] if j != i]
        seen: dict = {}
        left = [MAX_TIE_NODES]

        def walk(done):
            left[0] -= 1
            if left[0] < 0:
                raise _TooMany
            ahead = sum(self._ahead(self.w[j], self.g[j], self.w[i], gt) for j in pool)
            can = sum(self._level(self.w[j] + self.free[j], self.g[j], self.w[i], gt) for j in pool)
            if ahead >= self.spots or can < self.spots:
                return
            k, side = self._next_game(i)
            if k is None:
                level = [j for j in pool if self._level(self.w[j], self.g[j], self.w[i], gt)]
                if len(level) < self.spots:
                    return
                front = tuple(j for j in level if self._ahead(self.w[j], self.g[j], self.w[i], gt))
                key = (front, tuple(j for j in level if j not in front))
                try:
                    verdict = self._verdict(i)
                    code, shown = self._deciding_step(i)
                except _Need as need:
                    # Games nobody in the tie plays are played out only close
                    # to the end, and only while the search has room for them.
                    if sum(self.open) > MAX_TIE_OPEN or left[0] < MAX_TIE_NODES // 2:
                        seen.setdefault(key, []).append((dict(done), "open", need.code, None))
                        return
                    k = next(g for g in self.of[need.team] if self.open[g])
                    side = need.team
                else:
                    seen.setdefault(key, []).append((dict(done), verdict, code, shown))
                    return
            better = self._other(k, side)
            for winner in (better, self._other(k, better)):
                self._set(k, winner)
                done[k] = winner
                try:
                    walk(done)
                finally:
                    del done[k]
                    self._unset(k, winner)

        try:
            for finish in finishes:
                for k, w in finish.items():
                    self._set(k, w)
                try:
                    walk(dict(finish))
                finally:
                    for k, w in finish.items():
                        self._unset(k, w)
        except _TooMany:
            return None
        if len(seen) > MAX_TIE_LINES:
            return None
        out = [self._tie_entry(i, key, leaves) for key, leaves in seen.items()]
        order = {"in": 0, "depends": 1, "open": 2, "out": 3}
        out.sort(key=lambda e: (len(e["ahead"]) + len(e["level"]), order[e["verdict"]],
                                e["ahead"], e["level"]))
        return out

    def _tie_entry(self, i, key, leaves) -> dict:
        """One way to tie, from every way the games reach it."""
        front, level = key
        verdicts = {v for _, v, _, _ in leaves}
        entry = {"ahead": [self.teams[j] for j in front], "level": [self.teams[j] for j in level],
                 "verdict": verdicts.copy().pop() if len(verdicts) == 1 else "depends",
                 "step": None, "shown": None, "team": self.teams[i], "ways": None, "else": None}
        codes = {c for _, _, c, _ in leaves}
        if entry["verdict"] != "depends":
            if len(codes) == 1:
                entry["step"] = next(iter(codes))
                shows = [s for _, _, _, s in leaves]
                if shows[0] and all(s == shows[0] for s in shows):
                    entry["shown"] = {self.teams[j]: v for j, v in shows[0].items()}
            return entry
        # The results it turns on: the games settled in every way to this tie,
        # leaving out those it forces, since those go with the tie itself.
        common = set.intersection(*(set(d) for d, _, _, _ in leaves))
        ks = sorted(k for k in common if len({d[k] for d, _, _, _ in leaves}) > 1)
        yes = [d for d, v, _, _ in leaves if v == "in"]
        no = [d for d, v, _, _ in leaves if v != "in"]
        if not yes or not ks or len(ks) > MAX_SCENARIO_GAMES:
            return entry
        picked = _separate(yes, no, ks)
        if picked:
            ways = []
            for way in picked:
                results = []
                for k, w in way:
                    a, b = self.slate[k]
                    results.append((self.teams[w], self.teams[b if w == a else a]))
                ways.append(results)
            entry["ways"] = ways
            entry["else"] = "open" if "open" in verdicts else "out"
        return entry

    def met(self, t, other) -> tuple:
        """What ``t`` has done against ``other`` so far: wins, then losses."""
        a, b = self.ix[t], self.ix[other]
        return self.res[a][b], self.res[b][a]

    def path(self, t, week=None) -> dict | None:
        """What ``t`` needs, for a team still in the race: for each record it
        can finish with, whether that puts it in, and if it takes help, what
        help. None for a team that is in, is out or has no race. Every page that
        names the team asks, so the answer is kept."""
        if (t, week) not in self._paths:
            self._paths[(t, week)] = self._path(t, week)
        return self._paths[(t, week)]

    def _path(self, t, week):
        if self.status(t) is not None or not self.games[t] or self.fixed:
            return None
        i = self.ix[t]
        own = [k for k in self.of[i] if self.open[k]]
        n = len(own)
        rows, safe_bits = [], set()
        for wins in range(n, -1, -1):
            sets = [bits for bits in product((1, 0), repeat=n) if sum(bits) == wins]
            finishes = [self._results(own, bits, i) for bits in sets]
            safe = [self._safe(i, f) for f in finishes]
            live = [s or not self._dead(i, f) for s, f in zip(safe, finishes)]
            safe_bits.update(b for b, s in zip(sets, safe) if s)
            if all(safe):
                state = "in"
            elif any(safe):
                state = "some"
            elif any(live):
                state = "help"
            else:
                state = "out"
            final = self.w[i] + wins
            row = {"wins": wins, "losses": n - wins, "record": (final, self.g[i] - final),
                   "state": state}
            if state == "in":
                # In though others can match it: it holds the tiebreak.
                row["on_ties"] = any(self._records_leave_doubt(i, f) for f in finishes)
                if row["on_ties"]:
                    row["ties"] = self._ties(i, final, finishes,
                                             self._chasers(i, final, finishes)[0])
            if state in ("some", "help"):
                good = safe if state == "some" else live
                row["beats"] = self._must_beat(i, own, sets, good)
                keep = [f for f, g in zip(finishes, good) if g]
                chasing, already = self._chasers(i, final, keep)
                row["chasing"] = [self.teams[j] for j in chasing if j not in already]
                row["already"] = [self.teams[j] for j in already]
                row["same_games"] = all(self.g[j] == self.g[i] for j in chasing)
                row["otherwise"] = any(g and not s for s, g in zip(safe, live))
                if state == "help":
                    row["ways"] = self._help(i, keep)
                    row["beyond"] = self._beyond(i, final, keep, bool(row["ways"]))
                    row["ties"] = self._ties(i, final, keep, chasing)
            rows.append(row)
        return {
            "record": (self.wins[t], self.g[i] - self.wins[t] - n),
            "games": [(self.week_of[k], self.teams[self._other(k, i)]) for k in own],
            "rows": rows,
            "own": [[(self.teams[self._other(k, i)], bool(bit))
                     for k, bit in zip(own, term) if bit is not None]
                    for term in _cover(safe_bits, n)],
            "safe_at": _threshold(safe_bits, n),
            "control": (1,) * n in safe_bits,
            "week": self.this_week(t, week) if week is not None else [],
        }

    def _records_leave_doubt(self, i, fixed) -> bool:
        for k, w in fixed.items():
            self._set(k, w)
        doubt = self._others_pass(i, self._level)
        for k, w in fixed.items():
            self._unset(k, w)
        return doubt

    def _must_beat(self, i, own, sets, good):
        """The opponents every good finish beats, if that says it exactly."""
        chosen = [b for b, g in zip(sets, good) if g]
        if not chosen or len(chosen) == len(sets):
            return []
        must = [k for x, k in enumerate(own) if all(b[x] for b in chosen)]
        spots = {x for x, k in enumerate(own) if k in must}
        said = [b for b in sets if all(b[x] for x in spots)]
        if not must or len(said) != len(chosen):
            return None
        return [self.teams[self._other(k, i)] for k in must]

    def today(self) -> list[dict]:
        """How the title game would be set if the season ended today: for each
        race, the teams in and how each tie for a spot settles. A tie that
        reaches a step the results cannot settle stops there."""
        now = Race(set(self.teams), self._done, [], rules=self.rules,
                   divisions=self._divisions, overall=self.overall)
        now._totals_settled = True
        out = []
        names = sorted({d for d in now.div if d}) if self._divisions else [None]
        for div in names:
            pool = [j for j in range(now.n) if not div or now.div[j] == div]
            taken, ties = (), []
            while len(taken) < now.spots:
                grp = now._tied(pool, taken)
                if not grp:
                    break
                if len(grp) == 1:
                    taken += (grp[0],)
                    continue
                team_ids = [now.teams[j] for j in grp]
                place = len(taken) + 1
                if len(grp) <= now.spots - len(taken):
                    ties.append({"teams": team_ids, "place": place, "all_in": True, "steps": []})
                    taken += tuple(grp)
                    continue
                log = []
                got = now._pick(grp, div, log=log)
                ties.append({
                    "teams": team_ids, "place": place, "all_in": False,
                    "winner": now.teams[next(iter(got))] if len(got) == 1 else None,
                    "steps": [now._step_for_page(s) for s in log]})
                if len(got) > 1:
                    break
                taken += tuple(got)
            out.append({"division": div, "in": [now.teams[j] for j in taken],
                        "open": now.spots - len(taken), "ties": ties})
        return out

    def _step_for_page(self, step):
        out = {"code": step["code"], "note": step.get("note"),
               "teams": [self.teams[j] for j in step["teams"]],
               "keep": [self.teams[j] for j in step["keep"]]}
        if "shown" in step:
            out["shown"] = {self.teams[j]: v for j, v in step["shown"].items()}
        if isinstance(out["note"], list):
            out["note"] = [self.teams[j] for j in out["note"]]
        if "dropped" in step:
            out["dropped"] = self.teams[step["dropped"]]
        return out

    def conf_record(self, t) -> tuple:
        """Conference wins and losses so far."""
        played = self.games[t] - sum(t in (a, b) for _, a, b in self.left)
        return self.wins[t], played - self.wins[t]


def _separate(yes, no, ks):
    """The fewest results that hold in every way in and in no way out, each as
    (game, winner). A tie rules out most combinations by itself, so ones that
    cannot happen are free to fall on either side. None if it takes too many
    to be worth saying."""
    results = sorted({(k, d[k]) for d in yes for k in ks})
    holds = lambda way, d: all(d[k] == w for k, w in way)
    left, chosen = list(yes), []
    while left:
        best, covered = None, []
        for size in range(1, MAX_TIE_RESULTS + 1):
            for way in combinations(results, size):
                if len({k for k, _ in way}) < size or any(holds(way, d) for d in no):
                    continue
                got = [d for d in left if holds(way, d)]
                if len(got) > len(covered):
                    best, covered = way, got
            if best:
                break
        if not best or len(chosen) == MAX_TIE_WAYS:
            return None
        chosen.append(best)
        left = [d for d in left if not holds(best, d)]
    return chosen


def _threshold(hits, n):
    """The fewest wins that are always enough, when the number of wins is all
    that counts, as it is when any two of three will do."""
    if not hits:
        return None
    least = min(sum(b) for b in hits)
    every = {b for b in product((1, 0), repeat=n) if sum(b) >= least}
    return least if every == set(hits) else None


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


def _games(state, conf):
    """The conference's teams, its finished games as (winner, loser), its
    games left as (week, home, away), a title game already set, and every
    team's wins so far."""
    members = {t.idx for t in state.fbs_teams if t.conference == conf}
    done, left, title = [], [], None
    overall = Counter()
    for g in state.games:
        a, b = g["home_idx"], g["away_idx"]
        if g.get("completed") and not g.get("is_ccg"):
            overall[a if g["home_points"] > g["away_points"] else b] += 1
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
    return members, done, left, title, overall


# A build asks for each conference from every team's page, and the answers
# take a moment, so the last season's are kept.
_kept: tuple = (None, 0, {})


def conference_race(state, conf):
    """The title game race in ``conf``, or None where there is none to run."""
    global _kept
    stamp = sum(1 for g in state.games if g.get("completed"))
    if _kept[0] is not state or _kept[1] != stamp:
        _kept = (state, stamp, {})
    races = _kept[2]
    if conf not in races:
        members, done, left, title, overall = _games(state, conf)
        race = None
        if len(members) >= 3:
            rules = CR.title_rules(conf, state.year)
            divisions = None
            if rules.divisions:
                divisions = {t.idx: CR.division_of(conf, t.school, state.year)
                             for t in state.fbs_teams if t.idx in members}
            race = Race(members, done, left, title, rules, divisions,
                        {t: overall[t] for t in members})
        races[conf] = race
    return races[conf]


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
