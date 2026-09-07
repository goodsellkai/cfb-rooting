"""A fabricated season in the exact shape of the CFBD payloads.

This exists so the simulator, the statistics and the web app can be developed
and tested end to end without an API key, and so the test suite never needs
network access. It is not a data source -- nothing here is real.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

CONFERENCES = [
    # (name, abbreviation, team count, divisions, power?)
    ("SEC", "SEC", 16, [], True),
    ("Big Ten", "B1G", 18, [], True),
    ("Big 12", "B12", 16, [], True),
    ("ACC", "ACC", 17, [], True),
    ("Pac-12", "PAC", 8, [], False),
    ("American Athletic", "AAC", 14, [], False),
    ("Mountain West", "MWC", 12, [], False),
    ("Sun Belt", "SBC", 14, ["East", "West"], False),
    ("Mid-American", "MAC", 12, [], False),
    ("Conference USA", "CUSA", 10, [], False),
    ("FBS Independents", "IND", 3, [], False),
]


def build_payloads(seed: int = 7, year: int = 2026, weeks: int = 14,
                   played_through: int = 0) -> dict:
    """Return ``teams``, ``conferences``, ``games`` and ``fpi`` payloads.

    ``played_through`` marks every game up to and including that week as
    complete, with scores drawn from the same model the simulator uses.
    """
    rng = np.random.default_rng(seed)

    conferences_raw = []
    teams_raw = []
    tid = 1
    conf_members: dict[str, list[dict]] = {}

    for ci, (name, abbr, n, divisions, power) in enumerate(CONFERENCES):
        conferences_raw.append({
            "id": ci + 1, "name": name, "abbreviation": abbr,
            "short_name": abbr, "classification": "fbs", "member_count": n,
        })
        members = []
        # Power conferences are stronger on average and more top-heavy.
        centre = 12.0 if power else (2.0 if abbr in ("AAC", "MWC") else -4.0)
        spread = 9.0 if power else 6.5
        for k in range(n):
            rating = float(rng.normal(centre, spread))
            division = divisions[k % len(divisions)] if divisions else None
            row = {
                "id": tid, "school": f"{abbr} Team {k + 1:02d}",
                "mascot": "Sim", "abbreviation": f"{abbr}{k + 1:02d}",
                "conference": name if abbr != "IND" else "FBS Independents",
                "division": division, "classification": "fbs",
                "color": "#2b6cb0", "logos": None,
            }
            teams_raw.append(row)
            members.append({"row": row, "rating": rating, "division": division})
            tid += 1
        conf_members[name] = members

    fpi_raw = [{"year": year, "team": m["row"]["school"],
                "conference": m["row"]["conference"], "fpi": round(m["rating"], 2)}
               for members in conf_members.values() for m in members]
    rating_by_school = {r["team"]: r["fpi"] for r in fpi_raw}

    # A pool of FCS opponents for week-1 style buy games.
    fcs = [{"id": 9000 + i, "school": f"FCS Team {i:02d}", "classification": "fcs",
            "conference": "Missouri Valley"} for i in range(1, 31)]

    games_raw = []
    gid = 100000
    start = dt.datetime(year, 8, 29, tzinfo=dt.timezone.utc)

    def add_game(home, away, week, *, neutral=False, conference_game=False,
                 notes="", home_rating=None, away_rating=None):
        nonlocal gid
        gid += 1
        hr = rating_by_school.get(home["school"], -32.0) if home_rating is None else home_rating
        ar = rating_by_school.get(away["school"], -32.0) if away_rating is None else away_rating
        completed = week <= played_through
        hp = ap = None
        if completed:
            mu = hr - ar + (0.0 if neutral else 2.2)
            margin = rng.normal(mu, 16.5)
            if abs(margin) < 1:
                margin = 1.0 if margin >= 0 else -1.0
            base = max(10, int(rng.normal(27, 8)))
            hp = int(max(0, round(base + margin / 2)))
            ap = int(max(0, round(base - margin / 2)))
            if hp == ap:
                hp += 3
        games_raw.append({
            "id": gid, "season": year, "week": week, "season_type": "regular",
            "start_date": (start + dt.timedelta(days=7 * (week - 1))).isoformat(),
            "start_time_tbd": False, "completed": completed,
            "neutral_site": neutral, "conference_game": conference_game,
            "home_id": home["id"], "home_team": home["school"],
            "home_conference": home.get("conference"),
            "home_classification": home.get("classification", "fbs"),
            "home_points": hp,
            "away_id": away["id"], "away_team": away["school"],
            "away_conference": away.get("conference"),
            "away_classification": away.get("classification", "fbs"),
            "away_points": ap,
            "notes": notes, "venue": "Sim Stadium",
        })

    # -- conference schedules: a round-robin slice, one game per week -----
    for name, members in conf_members.items():
        rows = [m["row"] for m in members]
        n = len(rows)
        if n < 4:
            continue
        n_conf_games = 9 if n >= 14 else 8
        pad = rows + [None] if n % 2 else list(rows)
        m = len(pad)
        rot = list(range(m))
        for r in range(min(n_conf_games, m - 1)):
            week = r + 3  # conference play starts in week 3
            if week > weeks - 1:
                break
            for i in range(m // 2):
                a, b = rot[i], rot[m - 1 - i]
                ra, rb = pad[a], pad[b]
                if ra is None or rb is None:
                    continue
                home, away = (ra, rb) if (r + i) % 2 == 0 else (rb, ra)
                add_game(home, away, week, conference_game=True)
            rot = [rot[0]] + [rot[-1]] + rot[1:-1]

    # -- non-conference: FCS buy games and cross-conference matchups ------
    all_fbs = [m["row"] for members in conf_members.values() for m in members]
    for week in (1, 2, 12):
        pool = list(all_fbs)
        rng.shuffle(pool)
        for i, team in enumerate(pool):
            if week == 1 and i % 3 == 0:
                add_game(team, fcs[i % len(fcs)], week, away_rating=-32.0)
            elif i + 1 < len(pool) and i % 2 == 0:
                opp = pool[i + 1]
                if opp["conference"] != team["conference"]:
                    add_game(team, opp, week)

    return {
        "year": year,
        "teams": teams_raw,
        "conferences": conferences_raw,
        "games": games_raw,
        "fpi": fpi_raw,
        "sp": [],
    }


def synthetic_season(seed: int = 7, year: int = 2026, played_through: int = 0,
                     params=None):
    """Convenience wrapper returning a fully built :class:`SeasonState`."""
    from .season import build_season

    p = build_payloads(seed=seed, year=year, played_through=played_through)
    return build_season(year=p["year"], teams_raw=p["teams"],
                        conferences_raw=p["conferences"], games_raw=p["games"],
                        fpi_raw=p["fpi"], sp_raw=p["sp"], params=params)
