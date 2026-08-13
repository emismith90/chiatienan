#!/usr/bin/env python3
"""Generate PWA icons (icon-192.png, icon-512.png) for the Phoenix rebrand.

Rasterizes ``icon-phoenix.svg`` (same directory) — a white phoenix rising from
flame, on the app's #CC4E33 terracotta — chosen when the bot was reborn on a
new engine. The glyph stays inside the central ~70% so the maskable variants
survive Android's circle/squircle crops.

Requires cairosvg:  pip install cairosvg
"""
import os

try:
    import cairosvg
except ImportError:  # pragma: no cover
    raise SystemExit("cairosvg is required: pip install cairosvg")

HERE = os.path.dirname(__file__)
SVG = os.path.join(HERE, "icon-phoenix.svg")
OUT_DIR = os.path.join(HERE, "..", "public")


def make_icon(size: int, out_path: str) -> None:
    cairosvg.svg2png(url=SVG, write_to=out_path,
                     output_width=size, output_height=size)
    print(f"wrote {out_path} ({size}x{size})")


if __name__ == "__main__":
    make_icon(192, os.path.join(OUT_DIR, "icon-192.png"))
    make_icon(512, os.path.join(OUT_DIR, "icon-512.png"))
