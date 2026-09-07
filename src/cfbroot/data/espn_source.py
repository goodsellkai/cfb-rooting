"""ESPN's public power-index endpoint.

CFBD mirrors FPI, but its copy can lag ESPN by days -- early in the 2026 season
CFBD still carried preseason numbers while ESPN had already updated. Since FPI
is ESPN's metric, ESPN is the authoritative source and this is the primary
ratings fetcher.

Conveniently, CollegeFootballData uses ESPN's team ids, so ratings join to teams
on an exact integer key rather than on fuzzy school-name matching.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import cache

POWERINDEX_URL = (
    "https://site.web.api.espn.com/apis/fitt/v3/sports/football/college-football"
    "/powerindex?region=us&lang=en&contentorigin=espn&season={year}"
    "&limit={limit}&page={page}"
)
TTL = 3 * 3600
_UA = {"User-Agent": "Mozilla/5.0 (compatible; cfbroot/0.1)"}


class ESPNError(RuntimeError):
    pass


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ESPNError(f"ESPN power index request failed: {exc}") from exc


def _category_names(payload: dict) -> dict[str, list[str]]:
    """Map category name -> ordered stat names.

    Each team's ``values`` arrays are positional, and the labels for those
    positions live once at the top level rather than on every team.
    """
    out = {}
    for cat in payload.get("categories") or []:
        name = cat.get("name")
        names = cat.get("names")
        if name and names:
            out[name] = list(names)
    return out


def _extract(entry: dict, names: dict[str, list[str]]) -> dict | None:
    team = entry.get("team") or {}
    tid = team.get("id")
    if tid is None:
        return None
    row = {
        "espn_id": int(tid),
        "team": team.get("shortDisplayName") or team.get("nickname")
                or team.get("displayName"),
        "display_name": team.get("displayName"),
        "abbreviation": team.get("abbreviation"),
    }
    for cat in entry.get("categories") or []:
        cname = cat.get("name")
        labels = cat.get("names") or names.get(cname) or []
        values = cat.get("values") or []
        for label, value in zip(labels, values):
            if cname == "fpi" and label == "fpi":
                row["fpi"] = float(value)
            elif cname == "fpi" and label == "probmakeplayoffs":
                row["espn_playoff_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "probwinconf":
                row["espn_conf_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "probwintitle":
                row["espn_title_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "fpirank":
                row["fpi_rank"] = int(value)
    return row if "fpi" in row else None


def fetch_fpi(year: int, *, force: bool = False) -> dict:
    """Return ``{"last_updated": str|None, "rows": [...]}`` for the season."""

    def go() -> dict:
        rows: list[dict] = []
        page = 1
        last_updated = None
        names: dict[str, list[str]] = {}
        while page <= 20:
            payload = _get(POWERINDEX_URL.format(year=year, limit=200, page=page))
            if page == 1:
                names = _category_names(payload)
                last_updated = payload.get("lastUpdated")
            for entry in payload.get("teams") or []:
                row = _extract(entry, names)
                if row:
                    rows.append(row)
            pages = int((payload.get("pagination") or {}).get("pages") or 1)
            if page >= pages:
                break
            page += 1
        if not rows:
            raise ESPNError("ESPN returned no FPI rows")
        return {"last_updated": last_updated, "rows": rows}

    return cache.get_or_fetch("espn_fpi", {"year": year}, TTL, go, force=force)
