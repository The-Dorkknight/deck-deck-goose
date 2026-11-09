# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Makes every picture Deck Deck Goose ships from the one piece of artwork
(tools/art/deck-deck-goose.jpg): Steam tile art for Game Mode (Deck Deck Goose
and Airlock), the window banner, icons and the README banner.

    python3 tools/make_art.py

Needs Pillow. The wordmark is drawn with Inter Black Italic (SIL Open Font
License) if it is installed, otherwise with whatever bold font Pillow finds.
Re-run after replacing the artwork; commit the results.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "art" / "deck-deck-goose.jpg"
ASSETS = ROOT / "src" / "ddg" / "assets"
DOCS = ROOT / "docs" / "images"
ICONS = ROOT / "icons"

ORANGE, CYAN, AMBER, NAVY = (255, 138, 31), (37, 200, 255), (255, 207, 102), (10, 13, 31)
FONTS = ["/usr/share/fonts/opentype/inter/Inter-BlackItalic.otf",
         "/usr/share/fonts/truetype/google-fonts/Poppins-BoldItalic.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]



def small_png(img, path):
    """Saves a 256-colour (dithered) PNG: about a quarter of the size, looks the same on a handheld screen."""
    if img.mode == "RGBA":
        q = img.quantize(256, method=Image.FASTOCTREE, dither=Image.FLOYDSTEINBERG)
    else:
        q = img.convert("RGB").quantize(256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
    q.save(path, optimize=True)

def font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def wordmark(text: str, height: int, stops) -> Image.Image:
    """Chunky italic wordmark: gradient fill, dark outline, neon glow, transparent background."""
    size = height * 2
    f = font(size)
    pad = size // 3
    probe = ImageDraw.Draw(Image.new("L", (10, 10)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=f, stroke_width=size // 9)
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    sw = max(4, size // 11)
    outline, fill = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    ImageDraw.Draw(outline).text((pad - x0, pad - y0), text, font=f, fill=255, stroke_width=sw, stroke_fill=255)
    ImageDraw.Draw(fill).text((pad - x0, pad - y0), text, font=f, fill=255)
    grad = Image.new("RGB", (w, h))
    gp = grad.load()
    for x in range(w):
        t = (x - pad) / max(1, w - 2 * pad)
        c = stops[0][1]
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                c = lerp(c0, c1, (t - t0) / max(1e-6, t1 - t0))
            elif t > t1:
                c = c1
        for y in range(h):
            gp[x, y] = lerp(c, (255, 255, 255), 0.18 * max(0, 1 - abs(y - h * 0.42) / (h * 0.25)))
    glow = outline.filter(ImageFilter.GaussianBlur(size // 8))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (w, h), stops[len(stops) // 2][1] + (0,)), mask=glow.point(lambda v: int(v * 0.75)))
    out.paste(Image.new("RGBA", (w, h), NAVY + (255,)), mask=outline)
    out.paste(grad.convert("RGBA"), mask=fill)
    out = out.crop(out.getbbox())
    return out.resize((int(out.width * height / out.height), height), Image.LANCZOS)


TITLE = [(0.0, ORANGE), (0.325, ORANGE), (0.345, CYAN), (1.0, CYAN)]   # DECK orange, DECK GOOSE cyan, like the artwork
SOLID_O, SOLID_C = [(0.0, ORANGE), (1.0, ORANGE)], [(0.0, CYAN), (1.0, CYAN)]
AIR = [(0.0, AMBER), (1.0, ORANGE)]


def cover(img: Image.Image, w: int, h: int, box=None) -> Image.Image:
    if box:
        img = img.crop(box)
    r = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * r + 0.5), int(img.height * r + 0.5)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def tint(img: Image.Image, colour, amount=0.5) -> Image.Image:
    """Warm amber wash used to tell the Airlock tile from the app tile."""
    return Image.blend(img, ImageChops.multiply(img, Image.new("RGB", img.size, colour)), amount)


def fade_bottom(img: Image.Image, strength=0.85, start=0.55) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (1, h))
    for y in range(h):
        t = max(0.0, (y / h - start) / (1 - start))
        mask.putpixel((0, y), int(255 * strength * t))
    mask = mask.resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), NAVY), img, mask)


def paste_centre(base: Image.Image, logo: Image.Image, cx: int, y: int, width: int) -> None:
    lg = logo.resize((width, int(logo.height * width / logo.width)), Image.LANCZOS)
    base.paste(lg, (cx - lg.width // 2, y), lg)


def make(art: Image.Image, kind: str) -> None:
    air = kind == "airlock"
    pre = "airlock" if air else "gm"
    base = tint(art, (255, 224, 190)) if air else art
    logo = wordmark("AIRLOCK" if air else "DECK DECK GOOSE", 220, AIR if air else TITLE)
    W, H = base.size                                           # 1408 x 768

    # wide capsule 920x430 (Steam library list / recent games)
    wide = cover(base, 920, 430, (0, 8, W, 8 + int(W * 430 / 920)))
    if air:
        wide = fade_bottom(wide, 0.9, 0.62)
        paste_centre(wide, logo, 460, 330, 460)
    small_png(wide, ASSETS / (pre + "-wide.png"))

    # hero 1920x620 (game page background; the logo sits on top of it)
    hh = int(W * 620 / 1920)
    small_png(cover(base, 1920, 620, (0, 175, W, 175 + hh)), ASSETS / (pre + "-hero.png"))

    # portrait 600x900 (library grid): blurred, darkened copy as the backdrop, the whole artwork in the middle
    back = cover(base, 600, 900).filter(ImageFilter.GaussianBlur(18))
    port = ImageEnhance.Brightness(back).enhance(0.55)
    fg = base.resize((600, int(H * 600 / W)), Image.LANCZOS)
    if air:
        top = 300
        paste_centre(port, logo, 300, 90, 520)
        sub = wordmark("DESKTOP IN GAME MODE", 60, SOLID_C)
        paste_centre(port, sub, 300, top + fg.height + 40, 460)
    else:
        top = 395                                              # three big stacked words above the art
        for k, (word, st) in enumerate((("DECK", SOLID_O), ("DECK", SOLID_C), ("GOOSE", SOLID_C))):
            wm = wordmark(word, 104, st)
            paste_centre(port, wm, 300, 24 + k * 118, int(wm.width * (1.0 if word != "GOOSE" else 1.0)))
        sub = wordmark("YOUR GAMES, YOUR DRIVES", 50, [(0.0, ORANGE), (0.5, ORANGE), (0.52, CYAN), (1.0, CYAN)])
        paste_centre(port, sub, 300, top + fg.height + 50, 500)
    port.paste(fg, (0, top))
    small_png(port, ASSETS / (pre + "-portrait.png"))

    # logo (transparent) and icon
    big = logo.resize((1000, int(logo.height * 1000 / logo.width)), Image.LANCZOS)
    small_png(big, ASSETS / (pre + "-logo.png"))
    icon = base.crop((385, 150, 665, 430)).resize((256, 256), Image.LANCZOS)
    if air:                                                    # amber ring tells the Airlock icon apart
        ImageDraw.Draw(icon).rounded_rectangle((3, 3, 252, 252), radius=26, outline=AMBER, width=12)
    small_png(icon, ASSETS / (pre + "-icon.png"))


def main() -> int:
    if not SRC.exists():
        print("missing", SRC)
        return 1
    art = Image.open(SRC).convert("RGB")
    ASSETS.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    make(art, "ddg")
    make(art, "airlock")
    # window banner (full art, light weight) and README banner
    art.resize((1100, int(art.height * 1100 / art.width)), Image.LANCZOS).save(ASSETS / "hero.webp", quality=80)
    art.save(DOCS / "deck-deck-goose.webp", quality=88)
    # icons: the goose's head
    head = art.crop((385, 150, 665, 430))
    for name, px in (("icon-512.png", 512), ("apple-touch-icon.png", 180), ("favicon-64.png", 64)):
        for d in (DOCS, ICONS):
            small_png(head.resize((px, px), Image.LANCZOS), d / name)
    head.resize((96, 96), Image.LANCZOS).save(ASSETS / "icon.png", optimize=True)
    print("made", len(list(ASSETS.glob("*"))), "files in", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
