"""The committee's own view of each team, carried into the simulation.

Once the committee has published a ranking, where it disagrees with the model
is known rather than guessed. A team's gap is that disagreement in the model's
own units: the rating of the team the model has at the committee's place, less
the team's own. Most of a gap is still there in the committee's next ranking
and in its last, so a share of it is added to the team's rating on selection
day in every simulated season, and the noise that stood in for not knowing
the committee's mind is set to what is left once it is known.

Run ``python -m cfbroot.lean`` to measure both against the committee's polls
since 2014. It prints the numbers in ``selection.LEAN_SHARE`` and
``selection.LEAN_SD``. For every pair of polls in a season it takes the gaps
at the earlier one, adds a share to the model's rating at the later one, and
scores the order against what the committee published.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

from . import massey, selection

DEPTH = 25      # the committee publishes a top 25


def gaps(vals: dict, poll: dict) -> dict:
    """Each team's gap at one poll, in rating units.

    ``vals`` is the model's rating with the boosts in, ``poll`` the
    committee's place for the teams it ranked. A team it left out that the
    model has in its top 25 is taken to be 26th, the best it can be. One that
    neither ranks has no gap.
    """
    order = sorted(vals, key=lambda t: -vals[t])
    ladder = [vals[t] for t in order]
    mine = {t: i + 1 for i, t in enumerate(order)}
    out = {}
    for t, v in vals.items():
        if t in poll and poll[t] <= len(ladder):
            out[t] = ladder[poll[t] - 1] - v
        elif mine[t] <= DEPTH < len(ladder):
            out[t] = ladder[DEPTH] - v
        else:
            out[t] = 0.0
    return out


def _at(table, steps: int) -> float:
    return float(table[min(max(steps, 0), len(table) - 1)])


def share(steps: int) -> float:
    """How much of a gap is still there that many polls later."""
    return _at(selection.LEAN_SHARE, steps)


def noise(steps: int) -> float:
    """The committee noise that many polls after its last ranking."""
    return _at(selection.LEAN_SD, steps)


@dataclass
class Lean:
    """What the committee's latest ranking says about a season in progress."""

    week: int | None = None                     # the poll's week, None without one
    gap: dict = field(default_factory=dict)     # team index -> gap, in full
    left: int = 0                               # polls still to come after it
    poll: dict = field(default_factory=dict)    # team index -> the committee's place
    final: bool = False                         # it is the one the field came from
    rated: dict = field(default_factory=dict)   # team index -> the model's rating then

    @property
    def holds(self) -> bool:
        """Only the title games are left before the last ranking. The
        committee does not rank the rest again that week, so a team without a
        title game keeps the place it has, gap and all, and the two teams in
        each game move by what the game does to their ratings."""
        return self.week is not None and self.left == 1 and not self.final

    def settle(self, order: list, names: dict) -> list:
        """``order`` with the committee's own top 25 put first, as it ranked
        them. ``names`` turns a team index into whatever ``order`` holds."""
        top = [names[i] for i in sorted(self.poll, key=self.poll.get) if i in names]
        held = set(top)
        return top + [t for t in order if t not in held]

    def carried(self, steps: int | None = None) -> dict:
        """The gaps as they count that many polls on; on selection day if
        ``steps`` is left out."""
        if steps is None:
            s = 1.0 if self.holds else share(self.left)
        else:
            s = share(steps)
        return {i: s * v for i, v in self.gap.items()}

    def sd(self, base: float) -> float:
        """Committee noise on selection day. ``base`` is the setting without a
        poll, and scales the answer, so a caller that switched it off keeps
        it off."""
        if self.week is None or selection.COMMITTEE_SD <= 0:
            return base
        return base * noise(self.left) / selection.COMMITTEE_SD


def seen_week(state) -> int:
    """The last week whose games are all in, as the committee counts them.

    A game after selection, Army-Navy, does not hold it back, and the title
    games' week counts once every one of them has been played.
    """
    cut = massey.selection_week(state)
    pending = [g["week"] for g in state.games
               if g["status"] == 0 and not g["is_ccg"]
               and (cut is None or g["week"] < cut)]
    if pending or cut is None:
        return (min(pending) if pending else state.current_week()) - 1
    titles = [g for g in state.games if g["is_ccg"]]
    return cut if titles and all(g["status"] != 0 for g in titles) else cut - 1


def for_state(state) -> Lean:
    """The gaps from the newest committee ranking the season's results allow.

    A ranking carries the number of the week after the games it has seen, so
    one numbered more than a week past the last finished one has seen results
    this state does not have, and is passed over. That is what keeps the run
    that values last week's games from reading a ranking made after them.
    """
    polls = getattr(state, "cfp_polls", None) or {}
    seen = seen_week(state)
    # Kept on the state, since the rating behind it takes a second to fit.
    key = (seen, tuple(sorted(polls)))
    cached = getattr(state, "_lean", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    out = Lean()
    weeks = [w for w in polls if w <= seen + 1 and polls[w]]
    cut = massey.selection_week(state)
    if weeks and cut is not None:
        week = max(weeks)
        ratings = massey.rate_season(state, extra_games=state.fcs_games,
                                     through_week=week - 1)
        if ratings:
            ranked = selection.rank_teams(state, ratings, through_week=week - 1)
            names = {t.idx: t.school for t in state.teams}
            poll = {names[i]: r for i, r in polls[week].items() if i in names}
            by_school = gaps(ranked.ratings, poll)
            idx = {t.school: t.idx for t in state.fbs_teams}
            out = Lean(week=week,
                       gap={idx[s]: v for s, v in by_school.items() if s in idx and v},
                       left=max(0, cut + 1 - week),
                       poll={i: r for i, r in polls[week].items() if names.get(i) in idx},
                       final=week > cut,
                       rated={idx[s]: v for s, v in ratings.items() if s in idx})
    state._lean = (key, out)
    return out


# Measurement

LAMS = [round(float(x), 2) for x in np.arange(0.0, 1.21, 0.1)]
SDS = (0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12)
LAST_SDS = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08)
CUTS = (4, 8, 12, 16, 25)       # places whose odds the noise is scored on


def _season(year: int):
    from .backtest import committee_polls, fcs_games_for
    from .data.loader import load_season

    state = load_season(year)
    state.cfp_polls = {}        # the model alone; these are what it is scored against
    polls = committee_polls(year)
    fcs = fcs_games_for(year)
    weeks = sorted(polls)
    rated = {w: massey.rate_season(state, extra_games=fcs, through_week=w - 1)
             for w in weeks[:-1]}
    rated[weeks[-1]], _ = massey.rate_selection_day(state, extra_games=fcs)
    return state, polls, rated


def _places_off(order: list, poll: dict) -> tuple[float, int]:
    place = {t: i + 1 for i, t in enumerate(order)}
    teams = [t for t in poll if t in place]
    top = {t for t in teams if poll[t] <= 12}
    return (float(np.mean([abs(place[t] - poll[t]) for t in teams])),
            len(top & set(order[:12])))


def _log_loss(state, vals: dict, through: int, poll: dict, sd: float,
              draws: int, rng) -> float:
    """How well the odds of finishing inside each cut line come out with
    that much committee noise, as a log loss over the teams near the lines."""
    names = sorted(vals, key=lambda t: -vals[t])[:60]
    col = {t: j for j, t in enumerate(names)}
    hit = np.zeros((len(CUTS), len(names)))
    for _ in range(draws):
        order = selection.rank_teams(state, vals, rng=rng, committee_sd=sd,
                                     through_week=through).order
        for c, cut in enumerate(CUTS):
            for t in order[:cut]:
                if t in col:
                    hit[c, col[t]] += 1
    p = np.clip(hit / draws, 0.5 / draws, 1 - 0.5 / draws)
    loss = 0.0
    for c, cut in enumerate(CUTS):
        for t, j in col.items():
            inside = poll.get(t, DEPTH + 1) <= cut
            loss -= np.log(p[c, j] if inside else 1 - p[c, j])
    return float(loss)


def measure(years: list[int], draws: int = 60) -> None:
    rng = np.random.default_rng(1)
    off = {}        # (year, earlier week, later week) -> {share: (places off, top 12)}
    cases = {}      # the same key -> what the noise step needs
    for year in years:
        state, polls, rated = _season(year)
        weeks = sorted(polls)
        last = weeks[-1]
        boosted = {w: selection.rank_teams(state, rated[w], through_week=w - 1).ratings
                   for w in weeks}
        for a in weeks[:-1]:
            g = gaps(boosted[a], polls[a])
            for b in weeks:
                if b <= a:
                    continue
                res = {}
                for lam in LAMS:
                    moved = {t: v + lam * g.get(t, 0.0) for t, v in rated[b].items()}
                    res[lam] = _places_off(
                        selection.rank_teams(state, moved, through_week=b - 1).order,
                        polls[b])
                off[(year, a, b)] = res
                cases[(year, a, b)] = (state, g, rated[b], b - 1, polls[b], b == last)
        print(f"{year}: {len(weeks)} polls", flush=True)

    steps = sorted({b - a for _, a, b in off})
    best = {}
    print("\nPlaces off the committee's later ranking, by polls between the two")
    print("polls  pairs  model alone  with gap  share  seasons better/worse   "
          "top 12 alone  with   to the last poll only")
    for k in steps:
        keys = [key for key in off if key[2] - key[1] == k]
        seasons = sorted({key[0] for key in keys})
        if len(seasons) < 4:
            continue
        alone, carried, picks, hit0, hit1 = [], [], [], [], []
        for y in seasons:
            rest = [key for key in keys if key[0] != y]
            lam = min(LAMS, key=lambda v: np.mean([off[key][v][0] for key in rest]))
            mine = [key for key in keys if key[0] == y]
            picks.append(lam)
            alone.append(np.mean([off[key][0.0][0] for key in mine]))
            carried.append(np.mean([off[key][lam][0] for key in mine]))
            hit0.append(np.mean([off[key][0.0][1] for key in mine]))
            hit1.append(np.mean([off[key][lam][1] for key in mine]))
        best[k] = min(LAMS, key=lambda v: np.mean([off[key][v][0] for key in keys]))
        better = sum(c < a - 1e-9 for a, c in zip(alone, carried))
        worse = sum(c > a + 1e-9 for a, c in zip(alone, carried))
        final = [key for key in keys if cases[key][5]]
        to_last = min(LAMS, key=lambda v: np.mean([off[key][v][0] for key in final]))
        print(f"{k:>5} {len(keys):>6} {np.mean(alone):>12.2f} {np.mean(carried):>9.2f} "
              f"{best[k]:>6.1f} {better:>10}/{worse:<10} {np.mean(hit0):>10.2f} "
              f"{np.mean(hit1):>6.2f} {to_last:>12.1f}")

    print("\nCommittee noise that scores best once the gap is carried "
          "(log loss per pair of polls)")
    print("polls  " + "  ".join(f"{sd:.2f}" for sd in SDS) + "   best  held out")
    for k in sorted(best):
        keys = [key for key in cases if key[2] - key[1] == k]
        loss = {}
        for key in keys:
            state, g, vals, through, poll, _ = cases[key]
            moved = {t: v + best[k] * g.get(t, 0.0) for t, v in vals.items()}
            loss[key] = [_log_loss(state, moved, through, poll, sd, draws, rng)
                         for sd in SDS]
        total = np.mean([loss[key] for key in keys], axis=0)
        held = []
        for y in sorted({key[0] for key in keys}):
            rest = np.mean([loss[key] for key in keys if key[0] != y], axis=0)
            held.append(SDS[int(np.argmin(rest))])
        print(f"{k:>5}  " + "  ".join(f"{v:.1f}" for v in total)
              + f"   {SDS[int(np.argmin(total))]:.2f}  {np.mean(held):.3f}", flush=True)

    keys = [key for key in cases if cases[key][5] and key[2] - key[1] == 1]
    total = np.mean([[_log_loss(c[0], c[2], c[3], c[4], sd, draws, rng) for sd in SDS]
                     for c in (cases[key] for key in keys)], axis=0)
    print(" none  " + "  ".join(f"{v:.1f}" for v in total)
          + f"   {SDS[int(np.argmin(total))]:.2f}   (the last ranking, no gap carried)")
    measure_last(years, draws, rng)


def measure_last(years: list[int], draws: int, rng) -> None:
    """The step into the last ranking, where only the title games are played:
    holding every other team where the committee had it, against carrying
    the gap as in any other week, and against the committee's ranking left
    as it was. Seasons whose title games the data does not mark are left out."""
    same, carry, hold, loss = [], [], [], []
    for year in years:
        state, polls, rated = _season(year)
        weeks = sorted(polls)
        a, b = weeks[-2], weeks[-1]
        played = {t for pair in selection.title_game_pairs(state, b - 1) for t in pair}
        if not played:
            continue
        then, final = rated[a], rated[b]
        g = gaps(selection.rank_teams(state, then, through_week=a - 1).ratings, polls[a])
        same.append(float(np.mean([abs(polls[a].get(t, DEPTH + 1) - r)
                                   for t, r in polls[b].items()])))
        usual = {t: v + share(1) * g.get(t, 0.0) for t, v in final.items()}
        held = {t: (final[t] if t in played else then[t]) + g.get(t, 0.0) for t in final}
        for vals, out in ((usual, carry), (held, hold)):
            out.append(_places_off(
                selection.rank_teams(state, vals, through_week=b - 1).order, polls[b])[0])
        loss.append([_log_loss(state, held, b - 1, polls[b], sd, draws, rng)
                     for sd in LAST_SDS])
    print(f"\nInto the last ranking, places off it ({len(same)} seasons with title games marked)")
    print(f"   the ranking before it, left as it was   {np.mean(same):.2f}")
    print(f"   the gap carried as in any other week    {np.mean(carry):.2f}")
    print(f"   teams without a title game held         {np.mean(hold):.2f}")
    total = np.mean(loss, axis=0)
    print("   noise  " + "  ".join(f"{sd:.3f}" for sd in LAST_SDS))
    print("   loss   " + "  ".join(f"{v:5.1f}" for v in total)
          + f"   best {LAST_SDS[int(np.argmin(total))]:.3f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cfbroot.lean", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # 2020 is left out: a shortened season the model ranks 8.8 places off.
    ap.add_argument("--years", type=int, nargs="+",
                    default=[y for y in range(2014, 2026) if y != 2020])
    ap.add_argument("--draws", type=int, default=60)
    args = ap.parse_args(argv)
    measure(args.years, args.draws)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
