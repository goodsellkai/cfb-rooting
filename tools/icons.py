"""Draw the site mark: a yellow football on a field-green tile. The yellow is
the first-down line the site uses for the side to root for.

Run ``python tools/icons.py`` to rewrite logo.svg and every icon the pages
ask for. Needs Pillow, which the site itself does not. The SVG and the PNGs
come from the same numbers, so they match. Each PNG is drawn at eight times
its size and shrunk, so edges stay clean. Below 32 pixels the laces are left
off, since at that size they only muddy the ball.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "cfbroot" / "web" / "static"

# Geometry, in a 64 unit square.
R = 14.0            # corner radius of the tile
BALL_A = 23.0       # half length
BALL_B = 13.44      # half height
TILT = -22.0        # degrees
SEAM = 8.0          # half length of the seam line
TICK = 3.2          # half height of a lace tick
TICK_GAP = 4.0
STROKE = 3.0
# A maskable icon is cropped to a circle by the phone, so the ball shrinks to
# stay inside the middle 80% and the tile runs to the edges.
MASKABLE_SCALE = 0.86

TURF = (0x1c, 0x53, 0x36)
YELLOW = (0xff, 0xd2, 0x1f)


def hexof(c):
    return "#%02x%02x%02x" % c


def lens(a, b, steps=240):
    """An American football outline: two circular arcs meeting at the points."""
    r = (a * a + b * b) / (2 * b)
    cy = r - b
    span = math.asin(a / r)
    pts = []
    for i in range(steps + 1):                      # top arc, left to right
        t = -span + 2 * span * i / steps
        pts.append((r * math.sin(t), cy - r * math.cos(t)))
    for i in range(steps + 1):                      # bottom arc, back again
        t = span - 2 * span * i / steps
        pts.append((r * math.sin(t), -cy + r * math.cos(t)))
    return pts


def turn(pts, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def laces():
    """The seam and its four ticks, as line segments before the tilt."""
    out = [((-SEAM, 0.0), (SEAM, 0.0))]
    for i in (-1.5, -0.5, 0.5, 1.5):
        x = i * TICK_GAP
        out.append(((x, -TICK), (x, TICK)))
    return out


def draw(size, rounded=True, maskable=False):
    """The mark at ``size`` pixels, drawn big and shrunk down."""
    k = 8                                           # supersampling
    px = size * k
    unit = px / 64.0
    scale = MASKABLE_SCALE if maskable else 1.0
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded and not maskable:
        d.rounded_rectangle([0, 0, px - 1, px - 1], radius=R * unit, fill=TURF + (255,))
    else:
        d.rectangle([0, 0, px - 1, px - 1], fill=TURF + (255,))

    mid = px / 2.0

    def place(pts):
        return [(mid + x * unit * scale, mid + y * unit * scale)
                for x, y in turn(pts, TILT)]

    d.polygon(place(lens(BALL_A, BALL_B)), fill=YELLOW + (255,))
    if size >= 32:
        w = STROKE * unit * scale
        for a, b in laces():
            (ax, ay), (bx, by) = place([a, b])
            d.line([(ax, ay), (bx, by)], fill=TURF + (255,), width=round(w))
            for cx, cy in ((ax, ay), (bx, by)):     # round caps
                d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2],
                          fill=TURF + (255,))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def ball_path():
    r = (BALL_A ** 2 + BALL_B ** 2) / (2 * BALL_B)
    return (f"M {-BALL_A:g} 0 A {r:.3f} {r:.3f} 0 0 1 {BALL_A:g} 0 "
            f"A {r:.3f} {r:.3f} 0 0 1 {-BALL_A:g} 0 Z")


def svg():
    lines = "\n".join(
        f'      <line x1="{a[0]:g}" y1="{a[1]:g}" x2="{b[0]:g}" y2="{b[1]:g}"/>'
        for a, b in laces())
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"
     aria-label="CFB Rooting Guide">
  <rect width="64" height="64" rx="{R:g}" fill="{hexof(TURF)}"/>
  <g transform="translate(32 32) rotate({TILT:g})">
    <path d="{ball_path()}" fill="{hexof(YELLOW)}"/>
    <g stroke="{hexof(TURF)}" stroke-width="{STROKE:g}" stroke-linecap="round">
{lines}
    </g>
  </g>
</svg>
"""


def main():
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    where.mkdir(parents=True, exist_ok=True)
    (where / "logo.svg").write_text(svg(), encoding="utf-8")
    for size in (32, 48, 96, 180, 192, 512):
        draw(size).save(where / f"icon-{size}.png", optimize=True)
    draw(512, maskable=True).save(where / "icon-maskable-512.png", optimize=True)
    # iOS puts its own mask on, so that one is square and opaque.
    draw(180, rounded=False).convert("RGB").save(where / "apple-touch-icon.png",
                                                 optimize=True)
    small = [draw(s) for s in (16, 32)]
    draw(48).save(where / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                  append_images=small)
    print("wrote", where)


if __name__ == "__main__":
    main()
