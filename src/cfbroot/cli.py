"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from .config import DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES, SimConfig


def _load(args):
    from .data.loader import load_season
    return load_season(args.year, live=args.live, force=args.force,
                       synthetic=args.demo)


def cmd_serve(args) -> int:
    from .web.app import serve
    host, port = args.host, args.port
    print(f"cfbroot serving on http://{host}:{port}  (ctrl-c to stop)")
    serve(host=host, port=port, year=args.year)
    return 0


def cmd_export(args) -> int:
    from pathlib import Path

    from .web.export import export
    export(Path(args.out), year=args.year, n_sims=args.sims)
    return 0


def cmd_check(args) -> int:
    from .config import has_api_key
    from .data.cfbd_source import CFBDSource
    from .data.loader import default_year

    if not has_api_key():
        print("No CFBD_API_KEY found.\n"
              "  1. Get a free key at https://collegefootballdata.com/key\n"
              "  2. Copy .env.example to .env and paste the key in.\n"
              "Until then the app uses fake demo data.")
        return 1
    year = args.year or default_year()
    try:
        usage = CFBDSource(year).check()
    except Exception as exc:  # noqa: BLE001
        print(f"Key found, but the API call failed: {exc}")
        return 1
    print(f"API key works. Season {year}. Usage: {usage}")
    return 0


def cmd_guide(args) -> int:
    from rich.console import Console
    from rich.table import Table

    from .sim import build_guide, run

    console = Console()
    with console.status("loading season data..."):
        state = _load(args)

    week = None if args.all_weeks else (args.week or state.current_week())
    cfg = SimConfig(n_sims=args.sims, batch_size=min(25_000, args.sims),
                    seed=args.seed)

    with console.status(f"simulating {args.sims:,} seasons..."):
        res = run(state, args.team, cfg)
    guide = build_guide(state, res, primary=args.metric,
                        metrics=list(dict.fromkeys([args.metric, *DEFAULT_METRICS])),
                        week=week)

    h = guide.headline[guide.primary]
    console.print()
    console.rule(f"[bold]{guide.team}[/] | {state.year} | "
                 f"week {guide.week if week is not None else 'all remaining'}")
    console.print(f"[bold]{METRIC_LABELS[guide.primary]}: {h.p:.2%}[/]   "
                  f"expected wins {guide.expected_wins:.2f}   "
                  f"[dim]{res.n_sims:,} sims, {res.elapsed:.1f}s[/]")
    console.print()

    COLUMNS = ("Matchup", "Root for", "If away", "If home", "Swing", "")

    def add_rows(table, entries):
        for e in entries:
            s = e.swings[guide.primary]
            grade = ("[green]clear[/]" if s.significant
                     else ("[dim]thin[/]" if not s.reliable else "[dim]leaning[/]"))
            table.add_row(
                f"{e.away} {'vs' if e.neutral else '@'} {e.home}",
                f"[bold]{e.root_for}[/]",
                f"{s.p_if_away:.2%}",
                f"{s.p_if_home:.2%}",
                f"{'+' if s.delta >= 0 else '-'}{abs(s.delta) * 100:.2f}pp",
                grade)

    def make_table(title):
        t = Table(title=title, title_justify="left", box=None, pad_edge=False)
        for c in COLUMNS:
            t.add_column(c, justify="left" if c in ("Matchup", "Root for") else "right")
        return t

    if guide.own_games:
        t = make_table("Your games")
        add_rows(t, guide.own_games)
        console.print(t)
        console.print()

    t = make_table("Who to root for")
    add_rows(t, guide.games[:args.top])
    console.print(t)

    for n in guide.notes:
        console.print(f"[yellow]note[/] {n}")
    return 0


def cmd_massey(args) -> int:
    from rich.console import Console
    from rich.table import Table

    from .massey import rate_season, title_game_week

    console = Console()
    with console.status("loading season data..."):
        state = _load(args)

    ccg_week = title_game_week(state)
    through = args.through_week
    if through is None and ccg_week is not None:
        through = ccg_week - 1
    extra = []
    if not args.no_fcs:
        try:
            extra = state.fcs_games
            if not extra:
                raise RuntimeError("the season came without them")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]note[/] FCS games unavailable ({exc}); "
                          "every non-FBS opponent will share one rating.")
    ratings = rate_season(state, through_week=through, extra_games=extra,
                          use_scores=not args.bcs,
                          prior_from_rating=args.fpi_prior,
                          which="power" if args.power else "rating")
    if not ratings:
        print("No finished games yet.")
        return 1

    rec: dict[str, list[int]] = {}
    for g in state.games:
        if g["status"] == 0 or g["is_ccg"]:
            continue
        if through is not None and g["week"] > through:
            continue
        win, lose = ((g["home"], g["away"]) if g["status"] == 1
                     else (g["away"], g["home"]))
        rec.setdefault(win, [0, 0])[0] += 1
        rec.setdefault(lose, [0, 0])[1] += 1

    kind = "power" if args.power else "rating"
    basis = "win-loss only (BCS rules)" if args.bcs else "scores"
    console.rule(f"[bold]Massey {kind}[/] | {state.year}"
                 + (f" | through week {through}" if through is not None else "")
                 + f" | {basis}")
    t = Table(box=None, pad_edge=False)
    for c, j in (("#", "right"), ("Team", "left"), ("Conference", "left"),
                 ("Record", "right"), ("Rating", "right")):
        t.add_column(c, justify=j)
    by_school = {x.school: x for x in state.fbs_teams}
    for i, school in enumerate(sorted(ratings, key=lambda x: -ratings[x]), 1):
        if i > args.top:
            break
        w, l = rec.get(school, [0, 0])
        t.add_row(str(i), school, by_school[school].conference or "",
                  f"{w}-{l}", f"{ratings[school]:+.3f}")
    console.print(t)
    return 0


def cmd_refresh(args) -> int:
    from .data.loader import load_season
    state = load_season(args.year, live=True, force=True)
    print(f"{state.year}: {len(state.completed_games)} games played, "
          f"{len(state.remaining_games)} remaining. Week {state.current_week()}.")
    if state.diagnostics:
        print(state.diagnostics.summary())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cfbroot",
        description="Who should you root for this week in college football?")
    p.add_argument("--year", type=int, default=None, help="season (default: current)")
    p.add_argument("--force", action="store_true", help="bypass the disk cache")
    p.add_argument("--live", action="store_true",
                   help="use a short cache TTL for scores (game day)")
    p.add_argument("--demo", action="store_true",
                   help="use fake demo data instead of the API")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    e = sub.add_parser("export", help="write the app as a static site")
    e.add_argument("--out", default="site")
    e.add_argument("--sims", type=int, default=2_500_000)
    e.set_defaults(func=cmd_export)

    g = sub.add_parser("guide", help="print a rooting guide in the terminal")
    g.add_argument("--team", required=True)
    g.add_argument("--sims", type=int, default=100_000)
    g.add_argument("--week", type=int, default=None)
    g.add_argument("--all-weeks", action="store_true",
                   help="score all remaining games")
    g.add_argument("--metric", default="make_playoff", choices=METRIC_NAMES)
    g.add_argument("--top", type=int, default=20)
    g.add_argument("--seed", type=int, default=12345)
    g.set_defaults(func=cmd_guide)

    m = sub.add_parser("massey", help="print the Massey ratings")
    m.add_argument("--top", type=int, default=25)
    m.add_argument("--through-week", type=int, default=None,
                   help="last week to count (default: the week before the "
                        "conference title games)")
    m.add_argument("--power", action="store_true",
                   help="show the power rating instead of the overall rating")
    m.add_argument("--bcs", action="store_true",
                   help="ignore margin of victory, as the BCS required")
    m.add_argument("--no-fcs", action="store_true",
                   help="skip the FCS games, lumping all non-FBS into one team")
    m.add_argument("--fpi-prior", action="store_true",
                   help="start each team at its published rating instead of at "
                        "average, which steadies the first few weeks")
    m.set_defaults(func=cmd_massey)

    c = sub.add_parser("check", help="verify the CFBD API key")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("refresh", help="re-pull scores and ratings into the cache")
    r.set_defaults(func=cmd_refresh)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
