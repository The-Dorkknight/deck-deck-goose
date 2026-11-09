"""Regenerate src/icons/*.bgra from the PNGs (dev only, needs Pillow).

The overlay's X11 backend paints these raw premultiplied BGRA bytes directly,
so it needs no image library on the handheld:  python3 tools/make_raw_icons.py
"""
import os
from PIL import Image

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "icons")
for colour in ("green", "red"):
    im = Image.open(os.path.join(ICONS, f"zombie-brain-{colour}-128.png")).convert("RGBA")
    out = bytearray()
    px = im.tobytes()
    for i in range(0, len(px), 4):
        r, g, b, a = px[i:i + 4]
        out += bytes((b * a // 255, g * a // 255, r * a // 255, a))
    with open(os.path.join(ICONS, f"zombie-brain-{colour}-128.bgra"), "wb") as f:
        f.write(out)
    print(colour, len(out), "bytes")
