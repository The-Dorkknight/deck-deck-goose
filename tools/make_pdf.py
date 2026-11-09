# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Builds docs/DeckDeckGoose-manual.pdf from the README, HOW_IT_WORKS and CHANGELOG.

    python3 tools/make_pdf.py

Build-time only (not needed to run the app). Needs: pip install markdown playwright
(and a Chromium for Playwright). Run it before every release and commit the PDF;
the release workflow attaches it to the GitHub release.
"""
from __future__ import annotations

import re
import subprocess
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ddg import __build_date__, __version__  # noqa: E402

CSS = """
@page{size:A4;margin:16mm 15mm 18mm}
body{font:10.5pt/1.5 'Inter',system-ui,sans-serif;color:#141a38}
h1{color:#0a0d1f;font-size:20pt;border-bottom:3px solid #ff8a1f;padding-bottom:4pt;page-break-before:always}
h1:first-of-type{page-break-before:avoid}
h2{color:#0b7fb0;font-size:14pt;margin-top:16pt}h3{color:#b85c00}
img{max-width:100%;height:auto;border:1px solid #cfd6ee;border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:9pt;margin:6pt 0}td,th{border:1px solid #cfd6ee;padding:3pt 5pt;vertical-align:top}
th{background:#eef2ff}code{font-family:'DejaVu Sans Mono',monospace;font-size:8.6pt;background:#f1f4fc;padding:0 2pt;overflow-wrap:anywhere}
pre{background:#f1f4fc;padding:6pt;border-radius:6pt;white-space:pre-wrap}pre code{background:none}
.cover{height:250mm;page-break-after:always;text-align:center;background:#0a0d1f;color:#eaf2ff;margin:-16mm -15mm;padding:16mm 15mm}
.cover img{border:0;border-radius:10pt;margin-top:20mm}.cover h1{color:#25c8ff;border:0;font-size:30pt;page-break-before:avoid}
.cover p{color:#9aa8d0}.wip{background:#fff1d0;color:#6b4e00;font-weight:700;padding:5pt 8pt;border-radius:6pt;margin:8pt 0}
a{color:#0b7fb0}
"""


def md(text: str) -> str:
    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])


def shrink_images(html: str, folder: Path, width: int = 1000, quality: int = 72) -> str:
    """Screenshots go into the PDF as ~1000 px JPEGs: Chromium otherwise stores them losslessly (14 MB+)."""
    from PIL import Image
    folder.mkdir(exist_ok=True)

    def one(m):
        src = m.group(2)
        f = ROOT / src
        if not f.exists():
            return m.group(0)
        im = Image.open(f)
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        if bg.width > width:
            bg = bg.resize((width, round(bg.height * width / bg.width)), Image.LANCZOS)
        dest = folder / (f.stem + ".jpg")
        bg.save(dest, quality=quality, optimize=True, progressive=True)
        return m.group(1) + dest.relative_to(ROOT).as_posix() + m.group(3)
    return re.sub(r"""(<img[^>]*?src=['"])([^'"]+)(['"])""", one, html)


def main() -> int:
    from playwright.sync_api import sync_playwright
    readme = (ROOT / "README.md").read_text()
    readme = re.sub(r"^# .*\n\n!\[.*\]\(.*\)\n", "", readme, count=1)            # the cover replaces the banner
    readme = readme.replace("**WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN**", "")
    changelog = (ROOT / "CHANGELOG.md").read_text()
    changelog = "\n".join(changelog.split("\n## 0.2.1")[0].splitlines())          # recent versions only
    body = ("<div class='cover'><img src='docs/images/deck-deck-goose.webp' width='640'><h1>Deck Deck Goose</h1>"
            "<p>User manual - version %s (built %s)</p><div class='wip'>WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN</div>"
            "<p>Copyright Matthew Armstrong. PolyForm Noncommercial 1.0.0 (source-available). No warranty.</p></div>"
            % (__version__, __build_date__))
    body += md("# Deck Deck Goose\n" + readme) + md("# How it works\n" + (ROOT / "HOW_IT_WORKS.md").read_text().split("\n", 1)[1])
    body += md("# Changes\n" + changelog.split("\n", 1)[1])
    html = "<!doctype html><meta charset='utf-8'><style>%s</style>%s" % (CSS, body)
    page = ROOT / ".manual-build.html"                       # next to the images so relative paths work
    shrunk = ROOT / ".manual-img"
    html = shrink_images(html, shrunk)
    page.write_text(html)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(page.as_uri())
            pg.wait_for_timeout(500)
            out = ROOT / "docs" / "DeckDeckGoose-manual.pdf"
            pg.pdf(path=str(out), format="A4", print_background=True, margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"},
                   display_header_footer=True, header_template="<span></span>",
                   footer_template="<div style='font-size:8px;width:100%%;text-align:center;color:#667'>Deck Deck Goose %s - page <span class='pageNumber'></span> of <span class='totalPages'></span></div>" % __version__)
            b.close()
    finally:
        page.unlink()
        shutil.rmtree(shrunk, ignore_errors=True)
    if shutil.which("qpdf"):                                 # pack the PDF's own structure too (lossless)
        tmp = out.with_suffix(".tmp.pdf")
        if subprocess.run(["qpdf", "--object-streams=generate", "--compress-streams=y", "--recompress-flate",
                           str(out), str(tmp)]).returncode in (0, 3):
            tmp.replace(out)
        tmp.unlink(missing_ok=True)
    print("wrote", out, "(%d KB)" % (out.stat().st_size // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
