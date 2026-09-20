"""Turning a rating into a playoff field.

The rating says how good a team is. Getting from there to a bracket takes two
more things: the ordering rules the committee applies on top of a rating, and
the bid rules the playoff itself sets.

The 2026-27 format, which is what pick_field() implements:

  12 teams. Five automatic bids: the ACC, Big Ten, Big 12 and SEC champions,
  plus the highest ranked Group of Six team. That last one changed for 2026 --
  it goes to the best Group of Six team outright, champion or not, where 2024
  and 2025 gave the five bids to the five highest ranked conference champions.
  The other seven places are at large. Seeding is straight off the ranking, and
  the top four seeds get the byes.

The two twelve-team seasons before that ran different rules, which
playoff_format() records:

  2024  The five highest ranked conference champions got the automatic bids,
        and the four highest ranked champions took seeds 1-4 and the byes.
  2025  The same five champions, but straight seeding.

Seasons before 2024 had a four-team playoff, which the simulator does not
model; they get the 2024 rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FIELD = 12
N_BYES = 4
POWER = ("ACC", "Big Ten", "Big 12", "SEC")


def head_to_head_pairs(state, include_ccg: bool = False,
                       through_week: int | None = None) -> dict:
    """Who beat whom, as a set of (winner, loser) school pairs."""
    out = set()
    for g in state.games:
        if g["status"] == 0:
            continue
        if g["is_ccg"] and not include_ccg:
            continue
        if through_week is not None and g["week"] > through_week:
            continue
        w, l = ((g["home"], g["away"]) if g["status"] == 1
                else (g["away"], g["home"]))
        out.add((w, l))
    return out


def head_to_head_swap(order: list[str], beat: set, max_passes: int = 12
                      ) -> tuple[list[str], list[tuple[str, str, int]]]:
    """Swap neighbours when the lower team beat the one directly above it.

    Only adjacent pairs, because that is the case where the rating is already
    calling them near enough equal and the head to head result is the tiebreak.
    A team that beat someone ranked well above it does not leapfrog.

    One swap can create a new adjacency, so this repeats until nothing moves.
    Three teams that beat each other in a circle would swap forever, so the
    pass count is capped; the cap is not reached on any real season tried.
    """
    order = list(order)
    moves: list[tuple[str, str, int]] = []
    for _ in range(max_passes):
        swapped = False
        for i in range(len(order) - 1):
            hi, lo = order[i], order[i + 1]
            # Only swap on a clean result: if they split a pair of games,
            # neither beat the other outright.
            if (lo, hi) in beat and (hi, lo) not in beat:
                order[i], order[i + 1] = lo, hi
                moves.append((lo, hi, i + 1))
                swapped = True
        if not swapped:
            break
    return order, moves


@dataclass(frozen=True)
class PlayoffFormat:
    bids: str               # a pick_field() rule: "2026" or "2024"
    champion_byes: bool     # the top four champions take seeds 1-4


def playoff_format(year: int) -> PlayoffFormat:
    """The bid and seeding rules a season ran under."""
    if year >= 2026:
        return PlayoffFormat("2026", False)
    if year == 2025:
        return PlayoffFormat("2024", False)
    return PlayoffFormat("2024", True)


@dataclass
class Field:
    """A selected playoff field, seeded."""

    seeds: list[str]                      # seed 1 first
    auto: dict[str, str] = field(default_factory=dict)   # school -> why it is in
    byes: list[str] = field(default_factory=list)

    def __contains__(self, team: str) -> bool:
        return team in self.seeds

    def seed_of(self, team: str) -> int | None:
        return self.seeds.index(team) + 1 if team in self.seeds else None


def pick_field(order: list[str], champions: set, conference_of: dict,
               rule: str = "2026", size: int = FIELD, n_byes: int = N_BYES,
               power=POWER, champion_byes: bool = False) -> Field:
    """Select and seed the playoff field from a ranking.

    ``order`` is best first. ``champions`` is the set of conference champions.
    ``rule`` is "2026" for the current format, "2024" for the five highest
    ranked conference champions, which is what 2024 and 2025 used, or "none"
    for no automatic bids at all, which is what the four team playoff did.
    ``champion_byes`` gives seeds 1-4 to the four best ranked champions in the
    field, as 2024 did.
    """
    power = set(power)
    sel: list[str] = []
    why: dict[str, str] = {}

    def take(team, reason):
        if team not in why and len(sel) < size:
            sel.append(team)
            why[team] = reason

    if rule == "none":
        pass
    elif rule == "2026":
        for t in order:
            if t in champions and conference_of.get(t) in power:
                take(t, f"{conference_of[t]} champion")
        # The fifth bid is the best Group of Six team, title or no title.
        # Independents are not a Group of Six conference and cannot take it.
        for t in order:
            c = conference_of.get(t)
            if c and c not in power and "independent" not in c.lower():
                take(t, f"highest ranked Group of Six ({c})")
                break
    else:
        for t in order:
            if t in champions and len(sel) < 5:
                take(t, f"{conference_of.get(t, '?')} champion")

    for t in order:
        if len(sel) >= size:
            break
        take(t, "at large")

    # Straight seeding: the field is re-ordered by the ranking itself.
    place = {t: i for i, t in enumerate(order)}
    seeds = sorted(sel, key=lambda t: place.get(t, len(order)))
    if champion_byes:
        top = [t for t in seeds if t in champions][:n_byes]
        seeds = top + [t for t in seeds if t not in top]
    return Field(seeds=seeds, auto={t: why[t] for t in seeds},
                 byes=seeds[:n_byes])


TITLE_JUMP_MARGIN = 0.1


def title_game_pairs(state, through_week: int | None = None) -> list[tuple[str, str]]:
    """(winner, loser) for every conference title game that has been played."""
    out = []
    for g in state.games:
        if not g["is_ccg"] or g["status"] == 0:
            continue
        if through_week is not None and g["week"] > through_week:
            continue
        out.append((g["home"], g["away"]) if g["status"] == 1
                   else (g["away"], g["home"]))
    return out


def title_game_jump(order: list[str], ratings: dict, results: list,
                    margin: float = TITLE_JUMP_MARGIN
                    ) -> tuple[list[str], list[tuple[str, str, int, int]]]:
    """Move a title game winner in front of the team it beat, when it is close.

    The gap is read off the rating that already counts the title game, so the
    winner has its credit and the loser has had the loss dropped. If the loser
    is still ahead by more than ``margin`` it stays ahead: the rating is saying
    those two are not close, and winning one game does not overturn that. Inside
    the margin the teams are separated by less than noise, so the title game
    settles it.

    This runs after the head to head pass, so a conference title beats a regular
    season result between the same two teams. It is the later and the bigger
    game.
    """
    order = list(order)
    moves: list[tuple[str, str, int, int]] = []
    place = {t: i for i, t in enumerate(order)}
    pairs = [(w, l) for w, l in results if w in ratings and l in ratings
             and w in place and l in place]
    pairs.sort(key=lambda p: place[p[1]])     # settle the highest games first
    for w, l in pairs:
        place = {t: i for i, t in enumerate(order)}
        if place[w] < place[l]:
            continue                          # the winner is already ahead
        if ratings[l] - ratings[w] > margin:
            continue                          # not close; the loser earned it
        was = place[w] + 1
        order.remove(w)
        order.insert(order.index(l), w)
        moves.append((w, l, was, order.index(w) + 1))
    return order, moves


# Committee noise

# Standard deviation of the nudge added to each rating to stand in for the
# committee's own variability, in rating units. Set so a nudged ranking sits
# 1.35 places from the unnudged one on average through the top 25; at 0.050 the
# measured displacement is 1.34, which is about 1.3% of the rating's full
# spread.
#
# The committee's own rankings sit 2.99 places from ours, which would want
# 0.116. That figure counts every disagreement as chance, and much of it is
# not: the committee favours ACC and SEC teams by a few places, and a third of
# the gap is predictable from margin, schedule and conference. Calibrating to
# the whole gap would dress up a known bias as noise and swing seeds by five
# places, so the smaller figure is used.
COMMITTEE_SD = 0.050

# Worst loss. A team whose worst defeat came against a team near the top of
# the ranking is treated better than one that lost to nobody in particular,
# which is how the committee talks about "bad losses". The boost is
#
#     boost = WORST_LOSS_BOOST * exp(-(place of the weakest team it lost to - 1)
#                                    / WORST_LOSS_SCALE)
#
# so a team whose only loss is to the top team gets the full amount, one whose
# worst loss is to 13th gets about a third of it, and a loss to anyone outside
# the top 50 or so is worth nothing. A team that has not lost gets the full
# amount. Title game losses do not count, since a title game only helps.
#
# The size is deliberately small: swept against the committee's polls over
# 2023-25, 0.02 leaves the mean rank error where it was (2.95 places before
# the title games, 2.89 on selection day), while 0.06 and above cost a tenth
# of a place or more. It reorders the bubble without paying for it.
WORST_LOSS_BOOST = 0.02
WORST_LOSS_SCALE = 12.0


def worst_loss_place(state, order: list[str], through_week: int | None = None
                     ) -> dict[str, int]:
    """Where the weakest team each team lost to sits in ``order``.

    0 means it has not lost. A loss to a team that is not in the ranking at
    all, an FCS team, counts as the worst loss there is.
    """
    place = {t: i + 1 for i, t in enumerate(order)}
    worst = {t: 0 for t in order}
    for g in state.games:
        if g["status"] == 0 or g["is_ccg"]:
            continue
        if through_week is not None and g["week"] > through_week:
            continue
        w, l = ((g["home"], g["away"]) if g["status"] == 1
                else (g["away"], g["home"]))
        if l in worst:
            worst[l] = max(worst[l], place.get(w, len(order) + 1))
    return worst


def worst_loss_boost(state, order: list[str], ratings: dict,
                     through_week: int | None = None,
                     size: float = WORST_LOSS_BOOST,
                     scale: float = WORST_LOSS_SCALE) -> dict[str, float]:
    """``ratings`` with the worst-loss boost added."""
    if size <= 0:
        return dict(ratings)
    worst = worst_loss_place(state, order, through_week)
    return {t: v + size * (1.0 if not worst.get(t)
                           else math.exp(-(worst[t] - 1) / scale))
            for t, v in ratings.items()}


@dataclass
class Ranking:
    """An ordering, and what moved it off the raw rating."""

    order: list[str]
    ratings: dict[str, float]
    swaps: list = field(default_factory=list)
    jumps: list = field(default_factory=list)

    def rank_of(self, team: str) -> int | None:
        return self.order.index(team) + 1 if team in self.order else None


def rank_teams(state, ratings: dict, rng=None, committee_sd: float = COMMITTEE_SD,
               through_week: int | None = None) -> Ranking:
    """Order teams the way the committee is modelled to.

    The rating comes in, and four things happen to it: a nudge standing in for
    the committee's own variability, the worst-loss boost, the head to head
    pass, then the conference title game jump. Pass an ``rng`` to draw the
    nudge; leave it out and the ordering is deterministic, which is what a
    single published ranking wants.
    """
    vals = dict(ratings)
    if rng is not None and committee_sd > 0:
        vals = {t: v + rng.normal(0.0, committee_sd) for t, v in vals.items()}
    # The boost reads the order the ratings alone give, so it is worked out
    # once rather than chasing its own tail.
    vals = worst_loss_boost(state, sorted(vals, key=lambda t: -vals[t]), vals,
                            through_week)
    order = sorted(vals, key=lambda t: -vals[t])
    order, swaps = head_to_head_swap(
        order, head_to_head_pairs(state, through_week=through_week))
    order, jumps = title_game_jump(
        order, vals, title_game_pairs(state, through_week))
    return Ranking(order=order, ratings=vals, swaps=swaps, jumps=jumps)
