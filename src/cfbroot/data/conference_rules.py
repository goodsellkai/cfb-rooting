"""Each conference's title game rules: who gets in, how ties break, where it is.

Checked for 2026 against the conferences' own publications:

  SEC       secsports.com/fbtiebreaker (the October 2024 policy, still current)
  Big Ten   the 2024 divisionless policy, confirmed by the 2025 scenarios
  Big 12    the "2024-present" policy of November 2025
  ACC       the policy amended July 1, 2026, for the nine-game schedule
  Pac-12    the September 3, 2026 release for the rebuilt eight-team league
  Sun Belt  sunbeltsports.org football tie-breaking procedures
  American, Conference USA, MAC, Mountain West: each one's published order,
            which all run head-to-head, common opponents, then a ranking

Every conference sends its top two by conference winning percentage, except
the Sun Belt, which sends its East and West division winners. Ties go through
the steps below in order, one place at a time.

The last step in every conference is something the simulator does not have:
SportSource Analytics' ratings, the SEC's capped scoring margin, the CFP
ranking or a composite of computer rankings. The model's own rating stands in
for all of them.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tiebreak steps. The kernel reads these numbers; keep them in step with it.
END = 0
H2H = 1          # head-to-head among the tied teams
COMMON = 2       # record against all common conference opponents
NEXT = 3         # record against common opponents from the top of the standings down
OPP_PCT = 4      # combined conference winning percentage of conference opponents
TOTAL_WINS = 5   # total wins (Big 12)
DIV_PCT = 6      # record within the division (Sun Belt)
RATING = 7       # the rating, standing in for SportSource, the CFP ranking or computers
N_STEPS = 8      # columns in the step tables

# Flags
RESTART = 1      # a narrowed group of three or more starts over (SEC)
UNEQUAL = 2      # teams with more or fewer conference games can join a tie (ACC)
HOSTED = 4       # the title game is at the higher seed's stadium


@dataclass(frozen=True)
class TitleRules:
    two: tuple[int, ...]           # steps for a two-team tie
    multi: tuple[int, ...]         # steps for three or more
    flags: int = 0
    # division -> schools, for a conference that plays in divisions
    divisions: dict[str, tuple[str, ...]] | None = None


_POWER_ORDER = (H2H, COMMON, NEXT, OPP_PCT, RATING)
_GROUP_OF_SIX = (H2H, COMMON, NEXT, RATING)

_SUN_BELT_EAST = ("App State", "Coastal Carolina", "Georgia Southern", "Georgia State",
                  "James Madison", "Marshall", "Old Dominion")


def _sun_belt_west(year: int) -> tuple[str, ...]:
    # Texas State left for the Pac-12 in 2026 and Louisiana Tech came in.
    last = "Louisiana Tech" if year >= 2026 else "Texas State"
    return ("Arkansas State", "Louisiana", "UL Monroe", "South Alabama",
            "Southern Miss", "Troy", last)


def title_rules(conference: str | None, year: int) -> TitleRules:
    """The rules a conference's title race ran under in ``year``."""
    c = conference or ""
    if c == "SEC":
        return TitleRules(_POWER_ORDER, _POWER_ORDER, RESTART)
    if c == "Big Ten":
        return TitleRules(_POWER_ORDER, _POWER_ORDER)
    if c == "Big 12":
        order = (H2H, COMMON, NEXT, OPP_PCT, TOTAL_WINS, RATING)
        return TitleRules(order, order)
    if c == "ACC":
        if year >= 2026:
            # Head-to-head, then SportSource's Team Success Ranking. Five
            # teams play eight conference games in 2026 and the rest nine; an
            # eight-game team joins a nine-game leader's tie (or the other
            # way round) when it has as many conference wins or losses.
            return TitleRules((H2H, RATING), (H2H, RATING), UNEQUAL)
        return TitleRules(_POWER_ORDER, _POWER_ORDER)
    if c == "Pac-12":
        # The two-team order checks the best common opponents before all of
        # them; the multi-team order the other way round. The higher seed hosts.
        return TitleRules((H2H, NEXT, COMMON, RATING),
                          (H2H, COMMON, NEXT, OPP_PCT, RATING), HOSTED)
    if c == "Sun Belt":
        order = (H2H, DIV_PCT, NEXT, COMMON, RATING)
        return TitleRules(order, order, HOSTED,
                          {"East": _SUN_BELT_EAST, "West": _sun_belt_west(year)})
    if c in ("American Athletic", "Conference USA", "Mountain West"):
        return TitleRules(_GROUP_OF_SIX, _GROUP_OF_SIX, HOSTED)
    if c == "Mid-American":
        return TitleRules(_GROUP_OF_SIX, _GROUP_OF_SIX)   # Ford Field, Detroit
    return TitleRules((H2H, RATING), (H2H, RATING))


def division_of(conference: str | None, school: str, year: int) -> str | None:
    """A school's division, for the conferences that still have them."""
    rules = title_rules(conference, year)
    for name, schools in (rules.divisions or {}).items():
        if school in schools:
            return name
    return None


def step_row(steps: tuple[int, ...]) -> list[int]:
    """A step list padded to N_STEPS for the kernel's table."""
    row = list(steps)[:N_STEPS]
    return row + [END] * (N_STEPS - len(row))
