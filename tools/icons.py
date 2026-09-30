"""Draw the site mark: a football on a field-green tile.

Run ``python tools/icons.py`` to rewrite logo.svg and every icon the pages
ask for. Needs Pillow, which the site itself does not. The SVG and the PNGs
come from the same numbers, so they match. A favicon is drawn without the
stripes or the laces, which at that size only muddy it.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "cfbroot" / "web" / "static"

# Geometry, in a 64 unit square.
R = 14.0            # corner radius
BALL_A = 17.9       # half length
BALL_B = 10.9       # half height
TILT = -22.0        # degrees
SEAM = 6.1          # half length of the seam line
TICK = 2.3          # half height of a lace tick
TICK_GAP = 3.1
STROKE = 2.05

TOP = (0x35, 0x8a, 0x58)
BOTTOM = (0x1c, 0x53, 0x36)
STRIPE = (0x00, 0x00, 0x00, 11)
WHITE = (0xf7, 0xfa, 0xf8)
LACE = (0x1c, 0x53, 0x36)


def lens(a, b, steps=140):
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


def draw(size, rounded=True):
    """The tile at ``size`` pixels, drawn big and shrunk down.

    A favicon is mostly ball: at that size the margin is wasted and the
    stripes and laces only muddy it.
    """
    small = size <= 48
    a, b = (BALL_A * 1.15, BALL_B * 1.15) if small else (BALL_A, BALL_B)
    k = 8                                           # supersampling
    px = size * k
    unit = px / 64.0
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))

    field = Image.new("RGBA", (px, px))
    d = ImageDraw.Draw(field)
    for y in range(px):                             # top to bottom gradient
        f = y / (px - 1)
        d.line([(0, y), (px, y)],
               fill=tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * f) for i in range(3))
               + (255,))
    if size > 32:
        stripes = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ds = ImageDraw.Draw(stripes)
        band = px / 8.0                             # the mown stripes, faintly
        for i in range(0, 8, 2):
            ds.rectangle([0, round(i * band), px, round((i + 1) * band)],
                         fill=STRIPE)
        field = Image.alpha_composite(field, stripes)

    mask = Image.new("L", (px, px), 0)
    md = ImageDraw.Draw(mask)
    if rounded:
        md.rounded_rectangle([0, 0, px - 1, px - 1], radius=R * unit, fill=255)
    else:
        md.rectangle([0, 0, px - 1, px - 1], fill=255)
    img.paste(field, (0, 0), mask)

    d = ImageDraw.Draw(img)
    mid = px / 2.0

    def place(pts):
        return [(mid + x * unit, mid + y * unit) for x, y in pts]

    d.polygon(place(turn(lens(a, b), TILT)), fill=WHITE + (255,))

    def capsule(x1, y1, x2, y2, width):
        (ax, ay), (bx, by) = place(turn([(x1, y1), (x2, y2)], TILT))
        w = width * unit
        d.line([(ax, ay), (bx, by)], fill=LACE + (255,), width=round(w))
        for cx, cy in ((ax, ay), (bx, by)):
            d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2],
                      fill=LACE + (255,))

    if size > 40:
        capsule(-SEAM, 0, SEAM, 0, STROKE)
        for i in (-1.5, -0.5, 0.5, 1.5):
            x = i * TICK_GAP
            capsule(x, -TICK, x, TICK, STROKE)

    return img.resize((size, size), Image.LANCZOS)


SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"
     aria-label="CFB Rooting Guide">
  <defs>
    <linearGradient id="turf" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#{TOP[0]:02x}{TOP[1]:02x}{TOP[2]:02x}"/>
      <stop offset="1" stop-color="#{BOTTOM[0]:02x}{BOTTOM[1]:02x}{BOTTOM[2]:02x}"/>
    </linearGradient>
    <clipPath id="tile"><rect width="64" height="64" rx="{R:g}"/></clipPath>
  </defs>
  <g clip-path="url(#tile)">
    <rect width="64" height="64" fill="url(#turf)"/>
    <g fill="#000" opacity="{STRIPE[3] / 255:.3f}">
      <rect y="0" width="64" height="8"/><rect y="16" width="64" height="8"/>
      <rect y="32" width="64" height="8"/><rect y="48" width="64" height="8"/>
    </g>
  </g>
  <g transform="translate(32 32) rotate({TILT:g})">
    <path d="{{ball}}" fill="#{WHITE[0]:02x}{WHITE[1]:02x}{WHITE[2]:02x}"/>
    <g stroke="#{LACE[0]:02x}{LACE[1]:02x}{LACE[2]:02x}" stroke-width="{STROKE:g}"
       stroke-linecap="round">
      <line x1="{-SEAM:g}" y1="0" x2="{SEAM:g}" y2="0"/>
      <line x1="{-1.5 * TICK_GAP:g}" y1="{-TICK:g}" x2="{-1.5 * TICK_GAP:g}" y2="{TICK:g}"/>
      <line x1="{-0.5 * TICK_GAP:g}" y1="{-TICK:g}" x2="{-0.5 * TICK_GAP:g}" y2="{TICK:g}"/>
      <line x1="{0.5 * TICK_GAP:g}" y1="{-TICK:g}" x2="{0.5 * TICK_GAP:g}" y2="{TICK:g}"/>
      <line x1="{1.5 * TICK_GAP:g}" y1="{-TICK:g}" x2="{1.5 * TICK_GAP:g}" y2="{TICK:g}"/>
    </g>
  </g>
</svg>
"""


def ball_path():
    r = (BALL_A ** 2 + BALL_B ** 2) / (2 * BALL_B)
    return (f"M {-BALL_A:g} 0 A {r:.3f} {r:.3f} 0 0 1 {BALL_A:g} 0 "
            f"A {r:.3f} {r:.3f} 0 0 1 {-BALL_A:g} 0 Z")


def main():
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    where.mkdir(parents=True, exist_ok=True)
    (where / "logo.svg").write_text(SVG.replace("{ball}", ball_path()),
                                    encoding="utf-8")
    for size in (32, 48, 96, 180, 192, 512):
        draw(size).save(where / f"icon-{size}.png")
    # iOS puts its own mask on, so that one is square and opaque.
    draw(180, rounded=False).convert("RGB").save(where / "apple-touch-icon.png")
    small = [draw(s) for s in (16, 32)]
    draw(48).save(where / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                  append_images=small)
    print("wrote", where)


if __name__ == "__main__":
    main()
