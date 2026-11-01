"""Rebuild the README screenshots in docs/screenshots/ from one preprint.

    uv run python scripts/screenshots.py [OUTDIR]      (default: docs/screenshots)

* page1.png, figure-page.png: page 1 and the first full figure page, default theme.
* themes.png: every theme side by side (columns), each showing page 1 (front matter), page 2
  (body text and headings) and the first full figure page (figure, caption, running header).

Uses the README's example preprint, Ghosh et al., bioRxiv 10.64898/2026.10.16.759679
(CC BY 4.0), downloaded once into the cache.
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
from pathlib import Path

import typst
from PIL import Image, ImageDraw, ImageFont

from rxiv2pdf.compile import font_dirs, install_template
from rxiv2pdf.fetch import fetch
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.net import Client
from rxiv2pdf.render_typst import THEMES, ImageRef, RenderOptions, render
from rxiv2pdf.resolve import resolve

ROOT = Path(__file__).resolve().parent.parent
DOI = "10.64898/2026.10.16.759679"
PAGE_PPI, SHEET_PPI = 91.5, 56  # single pages ~793 px wide framed; the theme sheet ~1530 px wide
GAP, PAD, LABEL = 22, 28, 46  # theme sheet, px: between pages, around the sheet, label band
BACKGROUND, BORDER, INK = (226, 229, 233), (186, 190, 196), (40, 44, 52)


def colour_share(page: Image.Image) -> float:
    """Share of the page in saturated colour: high on a figure page, ~0 on text."""
    hist = page.convert("HSV").getchannel("S").histogram()
    return sum(hist[61:]) / sum(hist)


def key_pages(theme: str, ppi: float, article, manifest, version_dir: Path) -> list[Image.Image]:
    """Page 1, page 2 and the first full figure page of the preprint in `theme`."""
    assets = {f.href: ImageRef(f.path, f.width, f.height) for f in manifest.figures if f.status == "ok" and f.path}
    source, _ = render(article, assets=assets, options=RenderOptions(theme=theme))
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp)
        install_template(build)
        for rel in {ref.path for ref in assets.values()}:
            (build / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(version_dir / rel, build / rel)
        doc = build / "doc.typ"
        doc.write_text(source, encoding="utf-8")
        pngs = typst.compile(str(doc), format="png", ppi=ppi, root=str(build), font_paths=font_dirs(), ignore_system_fonts=True)
    pages = [Image.open(io.BytesIO(png)).convert("RGB") for png in pngs]
    # Typst places a figure where it fits, often pages after the text that cites it, so look
    # at the pages themselves: the first one that is mostly figure (the same figure in every theme).
    figure = next((p for p in pages[2:] if colour_share(p) > 0.08), pages[2])
    return [pages[0], pages[1], figure]


def framed(page: Image.Image, margin: int = 8) -> Image.Image:
    """A page on the sheet background, with a hairline edge (white pages vanish on white)."""
    out = Image.new("RGB", (page.width + 2 * margin, page.height + 2 * margin), BACKGROUND)
    out.paste(page, (margin, margin))
    ImageDraw.Draw(out).rectangle((margin - 1, margin - 1, margin + page.width, margin + page.height), outline=BORDER)
    return out


def save(image: Image.Image, target: Path) -> None:
    # 256 colours: a quarter of the size of full colour, and no visible loss at this scale
    image.quantize(colors=256, method=Image.Quantize.FASTOCTREE).save(target, optimize=True)
    print(target, image.size)


def theme_sheet(columns: dict[str, list[Image.Image]]) -> Image.Image:
    w, h = next(iter(columns.values()))[0].size
    rows = len(next(iter(columns.values())))
    size = (2 * PAD + len(columns) * w + (len(columns) - 1) * GAP, PAD + LABEL + rows * h + (rows - 1) * GAP + PAD)
    sheet = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(str(ROOT / "src" / "rxiv2pdf" / "fonts" / "SourceSans3-Semibold.ttf"), 26)
    for c, (theme, pages) in enumerate(columns.items()):
        x = PAD + c * (w + GAP)
        draw.text((x + w / 2, PAD + LABEL / 2 - 4), f"--theme {theme}", font=font, fill=INK, anchor="mm")
        for r, page in enumerate(pages):
            y = PAD + LABEL + r * (h + GAP)
            sheet.paste(page, (x, y))
            draw.rectangle((x - 1, y - 1, x + w, y + h), outline=BORDER)
    return sheet


def main() -> None:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    outdir.mkdir(parents=True, exist_ok=True)
    result = fetch(resolve(DOI), client=Client())
    article = parse_jats(result.xml_path.read_bytes(), meta=result.manifest)
    args = (article, result.manifest, result.version_dir)

    page1, _, figure = key_pages("default", PAGE_PPI, *args)
    save(framed(page1), outdir / "page1.png")
    save(framed(figure), outdir / "figure-page.png")
    save(theme_sheet({theme: key_pages(theme, SHEET_PPI, *args) for theme in THEMES}), outdir / "themes.png")


if __name__ == "__main__":
    main()
