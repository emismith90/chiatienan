#!/usr/bin/env python3
"""Generate PWA icons (icon-192.png, icon-512.png) for chiatienan.

Solid #CC4E33 background with a centered white glyph ("d" with a
horizontal strike, approximating the Vietnamese dong sign U+20AB).
Falls back to a simple white circle if no usable font glyph is found.
"""
import os

from PIL import Image, ImageDraw, ImageFont

BG_COLOR = (204, 78, 51, 255)  # #CC4E33
FG_COLOR = (255, 255, 255, 255)

GLYPH = "₫"  # dong sign

FONT_CANDIDATES = [
    "/System/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Arial.ttf",
]

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "public")


def find_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                # Verify the glyph actually renders (not a .notdef box).
                mask = font.getmask(GLYPH)
                if mask.getbbox() is not None:
                    return font
            except Exception:
                continue
    return None


def draw_fallback_bowl(draw, size):
    """Simple white circle/bowl shape as a fallback glyph."""
    margin = size * 0.28
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=FG_COLOR,
    )
    # Cut a smaller background-colored circle inside to form a ring (bowl look).
    inner_margin = size * 0.40
    draw.ellipse(
        [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
        fill=BG_COLOR,
    )


def make_icon(size, out_path):
    img = Image.new("RGBA", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_size = int(size * 0.55)
    font = find_font(font_size)

    if font is not None:
        bbox = draw.textbbox((0, 0), GLYPH, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) / 2 - bbox[0]
        y = (size - text_h) / 2 - bbox[1]
        draw.text((x, y), GLYPH, font=font, fill=FG_COLOR)
    else:
        draw_fallback_bowl(draw, size)

    img.save(out_path, format="PNG")
    print(f"wrote {out_path} ({size}x{size}, font={'yes' if font else 'fallback'})")


if __name__ == "__main__":
    make_icon(192, os.path.join(OUT_DIR, "icon-192.png"))
    make_icon(512, os.path.join(OUT_DIR, "icon-512.png"))
