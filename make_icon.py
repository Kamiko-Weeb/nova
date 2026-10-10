"""Generate Nova's application icon.

A nova is an exploding star, so the mark is a four-pointed burst. Run this once;
the .ico it writes is what the desktop and Start Menu shortcuts point at.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "nova.ico"

BG_OUTER = (19, 21, 26)       # matches the GUI background
BG_INNER = (31, 36, 48)
STAR = (118, 179, 255)        # the GUI accent blue
STAR_CORE = (226, 238, 255)
SIZE = 512                    # drawn large, downsampled for crispness


def rounded_square(draw: ImageDraw.ImageDraw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def burst(draw: ImageDraw.ImageDraw, cx, cy, arm, waist, fill):
    """A four-pointed star: long arms, pinched waist."""
    draw.polygon(
        [
            (cx, cy - arm),
            (cx + waist, cy - waist),
            (cx + arm, cy),
            (cx + waist, cy + waist),
            (cx, cy + arm),
            (cx - waist, cy + waist),
            (cx - arm, cy),
            (cx - waist, cy - waist),
        ],
        fill=fill,
    )


def build() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    pad = SIZE // 16
    rounded_square(draw, (pad, pad, SIZE - pad, SIZE - pad), SIZE // 6, BG_OUTER)
    inset = pad * 2
    rounded_square(
        draw, (inset, inset, SIZE - inset, SIZE - inset), SIZE // 8, BG_INNER
    )

    centre = SIZE // 2
    # Faint cross-glow behind the main burst.
    burst(draw, centre, centre, int(SIZE * 0.42), int(SIZE * 0.035), (40, 70, 120))
    burst(draw, centre, centre, int(SIZE * 0.34), int(SIZE * 0.07), STAR)
    burst(draw, centre, centre, int(SIZE * 0.17), int(SIZE * 0.05), STAR_CORE)

    # A small companion spark, off to the lower right.
    burst(
        draw,
        int(SIZE * 0.74),
        int(SIZE * 0.74),
        int(SIZE * 0.085),
        int(SIZE * 0.018),
        STAR,
    )
    return image


def main() -> None:
    image = build()

    sizes = [(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    image.save(OUT, format="ICO", sizes=sizes)
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {len(sizes)} sizes)")

    # macOS builds its .icns from this with iconutil, which needs 1024x1024.
    master = OUT.with_name("nova-1024.png")
    image.resize((1024, 1024), Image.LANCZOS).save(master, format="PNG")
    print(f"Wrote {master} (source for the macOS .icns)")

    preview = OUT.with_name("nova-icon-preview.png")
    image.resize((128, 128), Image.LANCZOS).save(preview)
    print(f"Preview: {preview}")


if __name__ == "__main__":
    main()
