"""Share cards: the picture a link preview shows for each page.

A preview is seen at a third of its size or less, in a chat or a feed, with the
page title and description already printed under it. So a card carries what
the text cannot: whose page it is, in that team's colours; one number, big;
and the one thing only this site says, which game to root for this week.

Cards are drawn with Pillow at twice their size and shrunk, in the site's own
typeface. A file name carries the build time, because chat apps keep an image
by its address and would otherwise go on showing an old number. Builds run
every half hour on a Saturday, so the day alone is not enough.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from ..config import CACHE_DIR
from . import pages

HERE = Path(__file__).resolve().parent
FONT = HERE / "static" / "fonts" / "archivo-latin.woff2"
W, H = 1200, 630
K = 2                          # drawn at twice the size, then shrunk
BAND = 488                     # the team-coloured top; a light strip below it
PAD = 72
INK = (15, 23, 18)
INK_2 = (57, 68, 61)
MARK = (255, 210, 31)          # the first-down yellow
TURF = (28, 83, 54)


def stamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%d%H%M")


def card_path(name: str, when: str) -> str:
    """Where a card lives, relative to the site root."""
    return f"card/{name}-{when}.jpg"


# Fonts and colour

@lru_cache(maxsize=None)
def font(size: int, width: float = 100, weight: float = 600):
    from PIL import ImageFont
    f = ImageFont.truetype(str(FONT), size * K)
    f.set_variation_by_axes([weight, width])      # the font lists weight first
    return f


def rgb(hexs: str | None):
    h = (hexs or "").lstrip("#")
    if len(h) != 6:
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def is_light(col) -> bool:
    """Whether dark text reads better than white on this colour."""
    r, g, b = (_lin(c / 255) for c in col)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 1.05 / (lum + 0.05) < (lum + 0.05) / 0.0587


def strip_colour(col) -> tuple:
    """The page's own tinted background for this team, as the site draws it."""
    r, g, b = (_lin(c / 255) for c in col)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    hue = math.atan2(bb, a)
    c = min(math.hypot(a, bb) * 0.11, 0.013)
    if 65 <= math.degrees(hue) % 360 <= 115:
        c *= 0.55
    L, A, B = 0.968, c * math.cos(hue), c * math.sin(hue)
    l_ = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
    m_ = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
    s_ = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
    out = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def srgb(x: float) -> int:
        x = min(1.0, max(0.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return round(255 * x)
    return tuple(srgb(x) for x in out)


# Logos, fetched once and kept

class Logos:
    """Team logos from ESPN's image server, kept on disk between builds."""

    def __init__(self, timeout: float = 10):
        self.timeout = timeout
        self.dir = CACHE_DIR / "logos"
        self.mem: dict[str, object] = {}

    def _file(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".png")

    def _fetch(self, url: str) -> None:
        path = self._file(url)
        if path.exists() or not url:
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cfbroot-cards"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
            self.dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception:                      # noqa: BLE001
            pass                               # the card draws without it

    def prefetch(self, urls) -> None:
        urls = [u for u in set(urls) if u]
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(self._fetch, urls))

    def get(self, url: str | None):
        """The logo as an RGBA image, or None if it could not be had."""
        if not url:
            return None
        if url not in self.mem:
            from PIL import Image
            self._fetch(url)
            try:
                img = Image.open(self._file(url)).convert("RGBA")
                self.mem[url] = img.crop(img.getbbox()) if img.getbbox() else img
            except Exception:                  # noqa: BLE001
                self.mem[url] = None
        return self.mem[url]


# Drawing

def _canvas(col, photo: int | None, strength: float):
    """The band in a colour, with a header photo greyed and blended into its
    right side the way the site's band does it, over a light strip."""
    from PIL import Image, ImageChops
    img = Image.new("RGB", (W * K, H * K), col)
    if photo:
        src = Image.open(HERE / "static" / "header" / f"{photo}.jpg").convert("L")
        bw, bh = W * K, BAND * K
        scale = max(bw / src.width, bh / src.height)
        src = src.resize((math.ceil(src.width * scale), math.ceil(src.height * scale)),
                         Image.Resampling.LANCZOS)
        top = int((src.height - bh) * 0.36)
        left = (src.width - bw) // 2
        grey = src.crop((left, top, left + bw, top + bh)).convert("RGB")
        base = Image.new("RGB", (bw, bh), col)
        blended = (ImageChops.screen(base, grey) if is_light(col)
                   else ImageChops.multiply(base, grey))
        # Faded in from the left, so the name and the number sit on clean colour.
        ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((bw, bh))
        lut = [int(255 * strength * min(1.0, max(0.0, (v / 255 - 0.38) / 0.4)))
               for v in range(256)]
        ramp = ramp.point(lut)
        img.paste(Image.composite(blended, base, ramp), (0, 0))
    return img


def _text(d, xy, text, f, fill, anchor="ls"):
    d.text((xy[0] * K, xy[1] * K), text, font=f, fill=fill, anchor=anchor)


def _len(text, f) -> float:
    return f.getlength(text) / K


def _fit(text, max_w, size, width, weight, floor):
    """The largest size up to ``size`` at which ``text`` fits ``max_w``."""
    while size > floor and _len(text, font(size, width, weight)) > max_w:
        size -= 2
    return font(size, width, weight)


def _tile(img, xy, side, logo, fallback: str):
    """A logo on a white rounded tile, as the site shows it."""
    from PIL import Image, ImageDraw
    x, y = (v * K for v in xy)
    s = side * K
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x + s, y + s], radius=int(s * 0.22), fill=(255, 255, 255))
    if logo is not None:
        inner = int(s * 0.78)
        lg = logo.copy()
        lg.thumbnail((inner, inner), Image.Resampling.LANCZOS)
        img.paste(lg, (x + (s - lg.width) // 2, y + (s - lg.height) // 2), lg)
    else:
        f = font(int(side * 0.34), 70, 820)
        d.text((x + s / 2, y + s / 2), fallback[:4], font=f, fill=INK, anchor="mm")


def _small_logo(img, xy, size, logo):
    from PIL import Image
    if logo is None:
        return 0
    lg = logo.copy()
    lg.thumbnail((size * K, size * K), Image.Resampling.LANCZOS)
    img.paste(lg, (int(xy[0] * K), int(xy[1] * K + (size * K - lg.height) / 2)), lg)
    return size


def _brand(img, d, right_x, base_y):
    """The mark and the name, set right to a point in the strip."""
    from PIL import Image
    f = font(34, 76, 800)
    name = pages.SITE_NAME
    tw = _len(name, f)
    _text(d, (right_x - tw, base_y), name, f, INK)
    mark = Image.open(HERE / "static" / "icon-192.png").convert("RGBA")
    m = 52 * K
    mark = mark.resize((m, m), Image.Resampling.LANCZOS)
    img.paste(mark, (int((right_x - tw - 66) * K), int((base_y - 42) * K)), mark)


def _strip_line(img, d, parts, x, base_y, max_w):
    """One line of mixed text in the strip: plain words, a highlighted team,
    and small logos. Shrinks together until it fits."""
    size = 44
    while True:
        plain = font(size, 88, 500)
        bold = font(size, 82, 780)
        lw = int(size * 1.0)

        def width():
            w = 0.0
            for kind, val in parts:
                if kind == "t":
                    w += _len(val, plain)
                elif kind == "hl":
                    w += _len(val, bold) + 22
                elif kind == "logo":
                    w += (lw + 10) if val is not None else 0
            return w
        if width() <= max_w or size <= 28:
            break
        size -= 2
    cx = x
    for kind, val in parts:
        if kind == "t":
            _text(d, (cx, base_y), val, plain, INK_2)
            cx += _len(val, plain)
        elif kind == "logo":
            cx += _small_logo(img, (cx, base_y - lw * 0.82), lw, val)
            if val is not None:
                cx += 10
        elif kind == "hl":
            tw = _len(val, bold)
            d.rounded_rectangle([cx * K, (base_y - size * 0.86) * K,
                                 (cx + tw + 22) * K, (base_y + size * 0.24) * K],
                                radius=6 * K, fill=MARK)
            _text(d, (cx + 11, base_y), val, bold, INK)
            cx += tw + 22


def _finish(img, path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((W, H), Image.Resampling.LANCZOS).save(path, "JPEG", quality=90,
                                           subsampling=0, optimize=True)


def draw_team(story: dict, team, colour, logos: Logos, index: dict, path: Path):
    """One team's card, from ``pages.card_story``."""
    from PIL import ImageDraw
    col = rgb(colour) or TURF
    fg = INK if is_light(col) else (255, 255, 255)
    img = _canvas(col, team.idx % 4 + 1, 0.5)
    d = ImageDraw.Draw(img)

    # Who: the logo tile, the name, the record and poll rank.
    _tile(img, (PAD, 60), 150, logos.get(team.logo), story["abbr"])
    nx = PAD + 150 + 34
    nf = _fit(team.school, W - nx - PAD, 104, 70, 820, 60)
    _text(d, (nx, 60 + 98), team.school, nf, fg)
    _text(d, (nx, 60 + 146), story["record"], font(38, 92, 500), fg)

    # The number, with what it is the chance of beside it.
    num, unit, label = story["number"], story["unit"], story["label"]
    for size in range(236, 120, -6):
        nfont = font(size, 64, 820)
        pfont = font(int(size * 0.52), 64, 760)
        lfont = font(46, 84, 650)
        nw = _len(num, nfont) + (4 + _len(unit, pfont) if unit else 0)
        lw = max(_len(s, lfont) for s in label)
        if nw + 30 + lw <= W - 2 * PAD:
            break
    base = 432
    _text(d, (PAD, base), num, nfont, fg)
    if unit:
        _text(d, (PAD + _len(num, nfont) + 4, base), unit, pfont, fg)
    lx = PAD + nw + 30
    _text(d, (lx, base - 52), label[0], lfont, fg)
    _text(d, (lx, base), label[1], lfont, fg)

    # The strip: this week's call, and the brand.
    d.rectangle([0, BAND * K, W * K, H * K], fill=strip_colour(col))
    parts = []
    for kind, val in story["line"]:
        if kind in ("hl", "t"):
            parts.append((kind, val))
        elif kind == "logo":
            parts.append(("logo", logos.get(index.get(val, {}).get("logo"))))
    _strip_line(img, d, parts, PAD, BAND + 88, W - 2 * PAD - 360)
    _brand(img, d, W - PAD, BAND + 86)
    _finish(img, path)


def draw_site(week: int, race, index_by_name: dict, logos: Logos, path: Path):
    """The card for the front page and the other pages that are not a team's."""
    from PIL import Image, ImageDraw
    img = _canvas(TURF, 3, 0.62)
    d = ImageDraw.Draw(img)
    mark = Image.open(HERE / "static" / "icon-192.png").convert("RGBA")
    m = 150 * K
    img.paste(mark.resize((m, m), Image.Resampling.LANCZOS), (PAD * K, 60 * K),
              mark.resize((m, m), Image.Resampling.LANCZOS))
    nx = PAD + 150 + 34
    _text(d, (nx, 60 + 98), pages.SITE_NAME, font(96, 72, 820), (255, 255, 255))
    _text(d, (nx, 60 + 146), "College Football Playoff odds for every team",
          font(38, 92, 500), (255, 255, 255))
    _text(d, (PAD, 420), f"Who to root for, week {week}",
          _fit(f"Who to root for, week {week}", W - 2 * PAD, 112, 66, 820, 70),
          (255, 255, 255))

    d.rectangle([0, BAND * K, W * K, H * K], fill=(243, 245, 242))
    parts = [("t", "Playoff odds:  ")]
    for i, (school, p) in enumerate(race[:3]):
        info = index_by_name.get(school, {})
        parts += [("logo", logos.get(info.get("logo"))),
                  ("t", f"{school} {100 * p:.2f}%" + ("   " if i < 2 else ""))]
    _strip_line(img, d, parts, PAD, BAND + 88, W - 2 * PAD)
    _finish(img, path)


class Cards:
    """Writes the cards for one build and says where each one is."""

    def __init__(self, out: Path, state, log=print):
        self.out = out
        self.stamp = stamp()
        self.logos = Logos()
        self.log = log
        self.index = {t.idx: {"logo": t.logo, "name": t.school}
                      for t in getattr(state, "teams", [])}
        self.by_name = {t.school: {"logo": t.logo} for t in getattr(state, "teams", [])}
        self.ok = True
        try:
            font(40)          # Pillow, and a FreeType that reads WOFF2
        except Exception as exc:               # noqa: BLE001
            log(f"  cards cannot be drawn ({exc}): pages keep the plain picture")
            self.ok = False
            return
        self.logos.prefetch(t.logo for t in getattr(state, "fbs_teams", []))

    def team(self, state, team, guide, week) -> str | None:
        """Draw a team's card; its address, or None to keep the plain one."""
        if not self.ok:
            return None
        rel = card_path(pages.slug(team.school), self.stamp)
        try:
            story = pages.card_story(state, team, guide, week)
            draw_team(story, team, team.color, self.logos, self.index, self.out / rel)
        except Exception as exc:               # noqa: BLE001
            self.log(f"  no card for {team.school}: {exc}")
            return None
        return f"{pages.SITE_URL}/{rel}"

    def site(self, week, race) -> str | None:
        if not self.ok:
            return None
        rel = card_path("site", self.stamp)
        try:
            draw_site(week, race, self.by_name, self.logos, self.out / rel)
        except Exception as exc:               # noqa: BLE001
            self.log(f"  no site card: {exc}")
            return None
        return f"{pages.SITE_URL}/{rel}"
