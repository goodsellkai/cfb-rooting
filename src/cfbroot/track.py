"""A private record of how the model does, kept out of the site.

Every build writes the model's win chance for each game still to come. Once a
game kicks off the build stops publishing it, so the chance it was judged on
would be gone. ``record`` keeps the last one each game had before kickoff,
then its result once it is played, along with every team's odds as each
build published them. The build runs it into a private repository; nothing
here is read by the site.

    python -m cfbroot.track record site/ tracking/    # after a build
    python -m cfbroot.track report tracking/          # how it has done

``games-<year>.json`` holds a game a key, ``"<week>|<away>|<home>"``, with the
first and last chance the model gave the home side and the score. Team names
are the key because indices only hold within one build.
``odds-<year>.jsonl`` holds a line a build: every team's chance of each
measure, in the order ``metrics`` gives.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .config import METRIC_NAMES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _key(week, away, home) -> str:
    return f"{week}|{away}|{home}"


def record(site: Path, into: Path, now: datetime | None = None) -> dict:
    """Fold one build's numbers into the record. Returns what changed."""
    now = now or _now()
    state = json.loads((site / "data" / "state.json").read_text("utf-8"))
    year = state["year"]
    names = {int(i): t["name"] for i, t in state["team_index"].items()}
    into.mkdir(parents=True, exist_ok=True)

    path = into / f"games-{year}.json"
    games = json.loads(path.read_text("utf-8")) if path.exists() else {}
    stamp = now.isoformat(timespec="minutes")
    seen = scored = 0

    # A chance counts only while the game is still to come, so a build that
    # lands after kickoff does not get to see how it started.
    for g in state.get("upcoming", []):
        if g.get("p_home") is None or not g.get("start"):
            continue
        if _when(g["start"]) <= now:
            continue
        home, away = names[g["home"]], names[g["away"]]
        p = round(g["p_home"], 4)
        row = games.setdefault(_key(g["week"], away, home), {
            "week": g["week"], "home": home, "away": away,
            "neutral": bool(g.get("neutral")),
            "first": {"p_home": p, "at": stamp},
        })
        row["start"] = g["start"]
        # Only a new number is written, with when it first appeared, so the
        # history of the file is the history of the chance.
        if row.get("last", {}).get("p_home") != p:
            row["last"] = {"p_home": p, "at": stamp}
        seen += 1

    for g in state.get("played", []):
        row = games.get(_key(g["week"], names[g["away"]], names[g["home"]]))
        if row is not None and "score" not in row:
            row["score"] = [g["home_points"], g["away_points"]]
            scored += 1

    path.write_text(json.dumps(games, indent=1, sort_keys=True) + "\n", "utf-8")

    # The whole league's table is in every team's file; any one will do.
    odds_lines = 0
    first = next(iter(sorted((site / "data" / "team").glob("*.json"))), None)
    if first is not None:
        league = json.loads(first.read_text("utf-8")).get("league", [])
        line = {"at": stamp, "build": state.get("build"),
                "week": state.get("current_week"), "metrics": METRIC_NAMES,
                "odds": {r["team"]: [round(r["p"].get(m, 0.0), 4) for m in METRIC_NAMES]
                         for r in league}}
        log = into / f"odds-{year}.jsonl"
        last = log.read_text("utf-8").splitlines()[-1] if log.exists() else ""
        if not last or json.loads(last).get("build") != line["build"]:
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
            odds_lines = 1
    return {"games": seen, "results": scored, "odds": odds_lines}


def report(into: Path, year: int | None = None) -> str:
    """How the last pregame chances held up, as plain text."""
    files = sorted(into.glob("games-*.json"))
    if year:
        files = [f for f in files if f.stem == f"games-{year}"]
    if not files:
        return "Nothing recorded yet."
    games = json.loads(files[-1].read_text("utf-8"))
    done = [g for g in games.values() if "score" in g and "last" in g
            and g["score"][0] != g["score"][1]]
    if not done:
        return f"{len(games)} games recorded, none played yet."

    brier = logloss = 0.0
    right = 0
    buckets: dict[int, list] = {}
    weeks: dict[int, list] = {}
    for g in done:
        p = g["last"]["p_home"]
        won = 1.0 if g["score"][0] > g["score"][1] else 0.0
        brier += (p - won) ** 2
        q = min(max(p, 1e-6), 1 - 1e-6)
        logloss -= won * math.log(q) + (1 - won) * math.log(1 - q)
        fav = max(p, 1 - p)
        fav_won = (p >= 0.5) == bool(won)
        right += fav_won
        b = min(int(fav * 10), 9)
        buckets.setdefault(b, []).append((fav, fav_won))
        weeks.setdefault(g["week"], []).append((p - won) ** 2)

    n = len(done)
    lines = [
        f"{n} games played with a pregame chance on record "
        f"({len(games) - n} still to come or tied).",
        f"Favourite won {right} of {n} ({100 * right / n:.1f}%).",
        f"Brier score {brier / n:.4f} (a coin flip scores 0.2500; lower is better).",
        f"Log loss {logloss / n:.4f} (a coin flip scores 0.6931).",
        "",
        "Favourite's chance   games   expected   won",
    ]
    for b in sorted(buckets):
        rows = buckets[b]
        exp = sum(f for f, _ in rows) / len(rows)
        won = sum(1 for _, w in rows if w) / len(rows)
        lines.append(f"  {10 * b:>2}-{10 * b + 10:<3}%          {len(rows):>5}     "
                     f"{100 * exp:5.1f}%   {100 * won:5.1f}%")
    lines += ["", "Week   games   Brier"]
    for w in sorted(weeks):
        lines.append(f"  {w:>3}   {len(weeks[w]):>5}   {sum(weeks[w]) / len(weeks[w]):.4f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m cfbroot.track",
                                description="A private record of how the model does.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="fold a build's numbers into the record")
    r.add_argument("site", type=Path, help="the built site")
    r.add_argument("into", type=Path, help="the tracking folder")
    s = sub.add_parser("report", help="how the model's chances have held up")
    s.add_argument("into", type=Path, help="the tracking folder")
    s.add_argument("--year", type=int)
    a = p.parse_args(argv)
    if a.cmd == "record":
        out = record(a.site, a.into)
        print(f"tracked {out['games']} games still to come, {out['results']} new "
              f"results, {out['odds']} odds snapshot")
    else:
        print(report(a.into, a.year))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
