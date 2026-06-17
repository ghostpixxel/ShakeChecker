"""Generate the flat retro Poke Ball app icon (matches the landing-page hero ball).

Run: python scripts/make_icon.py
Writes assets/icon_source.png (1024 px) and assets/shakechecker.ico (multi-size).
The exe icon is embedded from assets/shakechecker.ico via ShakeChecker.spec.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BLACK = (14, 14, 18, 255)
RED = (226, 69, 63, 255)
CREAM = (244, 239, 230, 255)
GLOSS = (255, 141, 136, 255)


def draw_ball(size: int = 1024, supersample: int = 3) -> Image.Image:
    """A flat, bold-outlined Poke Ball: red top, cream bottom, black band, centre
    button. Drawn large and downscaled for smooth edges."""
    n = size * supersample
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = n // 2
    r = int(0.46 * n)
    outline = int(0.085 * n)
    inner = r - outline
    band = int(0.058 * n)

    d.ellipse([c - r, c - r, c + r, c + r], fill=BLACK)  # outline disc

    # coloured halves + band + gloss, clipped to the inner circle
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rectangle([0, 0, n, c], fill=RED)
    ld.rectangle([0, c, n, n], fill=CREAM)
    ld.rectangle([0, c - band, n, c + band], fill=BLACK)
    ld.ellipse(
        [c - int(0.30 * n), c - int(0.34 * n), c - int(0.08 * n), c - int(0.18 * n)],
        fill=GLOSS,
    )
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).ellipse([c - inner, c - inner, c + inner, c + inner], fill=255)
    img.paste(layer, (0, 0), mask)

    for rad, col in ((int(0.15 * n), BLACK), (int(0.10 * n), CREAM), (int(0.05 * n), RED)):
        d.ellipse([c - rad, c - rad, c + rad, c + rad], fill=col)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ball = draw_ball()
    (ROOT / "assets").mkdir(exist_ok=True)
    ball.save(ROOT / "assets" / "icon_source.png")
    ball.save(
        ROOT / "assets" / "shakechecker.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("wrote assets/icon_source.png and assets/shakechecker.ico")


if __name__ == "__main__":
    main()
