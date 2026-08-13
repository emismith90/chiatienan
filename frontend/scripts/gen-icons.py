#!/usr/bin/env python3
"""Generate the app's icons from ``icon-phoenix.svg`` (same directory).

The art is a white phoenix rising from flame on the app's #CC4E33 terracotta,
with the đ (dong sign, kept from the pre-rebrand icon) cut into its chest —
chosen when the bot was reborn on a new engine. The đ is drawn as strokes, not
text, so no font is needed. The glyph stays inside the central ~70% so the
maskable variants survive Android's circle/squircle crops.

Outputs:
  public/icon-192.png          PWA / manifest
  public/icon-512.png          PWA / manifest, splash
  public/apple-touch-icon.png  180², the path iOS probes with no <link> to go on
  src/app/favicon.ico          16/32/48, the browser tab + the URL every
                               browser and link-preview bot requests blind.
                               Its small frames come from icon-favicon.svg
                               (the đ alone) — see that file.

WHY the .ico exists: the app shipped without one, so /favicon.ico 404'd in
production and the tab fell back to whatever the browser had cached from
before the rebrand. A <link rel="icon"> does not stop that request.

**Changing the art means bumping ICON_VERSION here and in the three places
that reference the icons** (manifest.webmanifest, layout.tsx, public/sw.js) —
the filenames are stable, so the ``?v=`` query is the only thing that tells a
browser favicon store, a service-worker cache, or an installed Android WebAPK
that the bytes moved. The Phoenix rebrand shipped new bytes at the old URLs and
every already-installed client kept showing the old icon.

Requires:  pip install cairosvg pillow
"""
import io
import os

try:
    import cairosvg
    from PIL import Image
except ImportError:  # pragma: no cover
    raise SystemExit("cairosvg and pillow are required: pip install cairosvg pillow")

ICON_VERSION = 2

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "icon-phoenix.svg")
SVG_SMALL = os.path.join(HERE, "icon-favicon.svg")
PUBLIC = os.path.join(HERE, "..", "public")
APP = os.path.join(HERE, "..", "src", "app")


def render(size: int, svg: str = SVG) -> Image.Image:
    png = cairosvg.svg2png(url=svg, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def write_png(size: int, out_path: str) -> None:
    render(size).save(out_path, format="PNG", optimize=True)
    print(f"wrote {os.path.normpath(out_path)} ({size}x{size})")


def write_ico(out_path: str, sizes=(16, 32, 48)) -> None:
    # Rasterize each size from its own SVG rather than downscaling one bitmap:
    # resampling the phoenix to 16px leaves a smudge, so the tab-sized frames
    # are drawn from the đ-only mark and only 48 carries the full bird.
    frames = [render(s, SVG if s >= 48 else SVG_SMALL) for s in sizes]
    frames[-1].save(out_path, format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"wrote {os.path.normpath(out_path)} ({'/'.join(str(s) for s in sizes)})")


if __name__ == "__main__":
    write_png(192, os.path.join(PUBLIC, "icon-192.png"))
    write_png(512, os.path.join(PUBLIC, "icon-512.png"))
    write_png(180, os.path.join(PUBLIC, "apple-touch-icon.png"))
    write_ico(os.path.join(APP, "favicon.ico"))
    print(f"\nicon version = {ICON_VERSION}: referenced as '?v={ICON_VERSION}' in "
          "public/manifest.webmanifest, src/app/layout.tsx and public/sw.js")
