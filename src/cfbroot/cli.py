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


def cmd_check(args) -> int:
    from .config import has_api_key
    from .data.cfbd_source import CFBDSource
    from .data.loader import default_year

    if not has_api_key():
        print("No CFBD_API_KEY found.\n"
              "  1. Get a free key at https://collegefootballdata.com/key\n"
              "  2. Copy .env.example to .env and paste the key in.\n"
              "Until then the app runs on fabricated demo data.")
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
                   help="use the fabricated demo season instead of the API")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    g = sub.add_parser("guide", help="print a rooting guide in the terminal")
    g.add_argument("--team", required=True)
    g.add_argument("--sims", type=int, default=250_000)
    g.add_argument("--week", type=int, default=None)
    g.add_argument("--all-weeks", action="store_true",
                   help="score every remaining game, not just this week's")
    g.add_argument("--metric", default="make_playoff", choices=METRIC_NAMES)
    g.add_argument("--top", type=int, default=20)
    g.add_argument("--seed", type=int, default=12345)
    g.set_defaults(func=cmd_guide)

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
