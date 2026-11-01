"""Rebuild the README screenshots in docs/screenshots/ from one preprint.

    uv run python scripts/screenshots.py [OUTDIR]      (default: docs/screenshots)

* before-after.png: pages 1-3 of the authors' PDF on bioRxiv (stacked, page 1 in front) next to
  page 1 of the rxiv2pdf PDF (default theme), which holds the same text.
* themes.png: every theme side by side (columns), each showing page 1 (front matter), page 2
  (body text and headings) and the first full figure page (figure, caption, running header).

Uses the README's example preprint, Ghosh et al., bioRxiv 10.64898/2026.10.16.759679
(CC BY 4.0), downloaded once into the cache.

Never delete or rename a file in docs/screenshots/ that a released README links to: PyPI
shows each release's README as it was, and the images load from the main branch. page1.png
and figure-page.png are no longer built or used here, but the 0.1.0 page on PyPI shows them.
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
from rxiv2pdf.fetch import fetch, fetch_original_pdf
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.net import Client
from rxiv2pdf.render_typst import THEMES, ImageRef, RenderOptions, render
from rxiv2pdf.resolve import resolve

ROOT = Path(__file__).resolve().parent.parent
DOI = "10.64898/2026.10.16.759679"
PAGE_PPI, SHEET_PPI = 91.5, 56  # before/after pages ~1000 px tall; the theme sheet ~1530 px wide
GAP, PAD, LABEL = 22, 28, 46  # sheets, px: between pages, around the sheet, label band
ARROW_GAP = 84  # before/after: between the two sides, room for the arrow
# Before/after: the manuscript pages stacked on the left, page 1 in front. rxiv2pdf's page 1
# holds what the manuscript's pages 1-3 hold (title page, abstract, start of the introduction).
# Each page further back sits right of and below the one in front, where the margins are blank
# (bioRxiv's notice at the top of every page would show cut off).
STACK, STACK_STEP = 3, (34, 24)  # pages; px offset per page
BACKGROUND, BORDER, INK = (226, 229, 233), (186, 190, 196), (40, 44, 52)
FONT = ROOT / "src" / "rxiv2pdf" / "fonts" / "SourceSans3-Semibold.ttf"


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


def pdf_pages(pdf: Path, count: int, height: int) -> list[Image.Image]:
    """The first ``count`` pages of ``pdf``, rendered by Typst (as for appended supplements),
    each ``height`` px tall."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copyfile(pdf, Path(tmp) / "original.pdf")

        def png(n: int, ppi: float) -> Image.Image:
            source = f'#set page(width: auto, height: auto, margin: 0pt)\n#image("original.pdf", page: {n})'.encode()
            return Image.open(io.BytesIO(typst.compile(source, root=tmp, format="png", ppi=ppi))).convert("RGB")

        pages = []
        for n in range(1, count + 1):
            points = png(n, 72).height  # 1 px per point
            page = png(n, 2 * 72 * height / points)  # twice the size, then scaled down: smoother text
            pages.append(page.resize((round(page.width * height / page.height), height), Image.Resampling.LANCZOS))
    return pages


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
    font = ImageFont.truetype(str(FONT), 26)
    for c, (theme, pages) in enumerate(columns.items()):
        x = PAD + c * (w + GAP)
        draw.text((x + w / 2, PAD + LABEL / 2 - 4), f"--theme {theme}", font=font, fill=INK, anchor="mm")
        for r, page in enumerate(pages):
            y = PAD + LABEL + r * (h + GAP)
            sheet.paste(page, (x, y))
            draw.rectangle((x - 1, y - 1, x + w, y + h), outline=BORDER)
    return sheet


def before_after(before: list[Image.Image], after: Image.Image) -> Image.Image:
    """The manuscript's pages stacked (first page in front) next to the rxiv2pdf page, labelled,
    with an arrow between them. The stack is as tall as ``after``."""
    (dx, dy), back = STACK_STEP, len(before) - 1
    stack_w = before[0].width + back * dx
    top = PAD + LABEL
    sheet = Image.new("RGB", (2 * PAD + stack_w + ARROW_GAP + after.width, top + after.height + PAD), BACKGROUND)
    draw = ImageDraw.Draw(sheet)

    def place(page: Image.Image, x: int, y: int) -> None:
        sheet.paste(page, (x, y))
        draw.rectangle((x - 1, y - 1, x + page.width, y + page.height), outline=BORDER)

    for i in reversed(range(len(before))):  # back to front: page 1 is pasted last, on top
        place(before[i], PAD + i * dx, top + i * dy)
    right = PAD + stack_w + ARROW_GAP
    place(after, right, top)

    font = ImageFont.truetype(str(FONT), 28)
    for centre, label in ((PAD + stack_w / 2, "Manuscript from bioRxiv"), (right + after.width / 2, "rxiv2pdf output")):
        draw.text((centre, PAD + LABEL / 2 - 4), label, font=font, fill=INK, anchor="mm")
    x0, x1, mid = PAD + stack_w + 18, right - 18, top + after.height // 2
    draw.line((x0, mid, x1 - 16, mid), fill=INK, width=6)
    draw.polygon([(x1, mid), (x1 - 22, mid - 15), (x1 - 22, mid + 15)], fill=INK)
    return sheet


def main() -> None:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"
    outdir.mkdir(parents=True, exist_ok=True)
    client = Client()
    result = fetch(resolve(DOI), client=client)
    article = parse_jats(result.xml_path.read_bytes(), meta=result.manifest)
    args = (article, result.manifest, result.version_dir)

    page1 = key_pages("default", PAGE_PPI, *args)[0]
    original, _ = fetch_original_pdf(result.resolved, client=client)  # the authors' PDF, cached
    manuscript = pdf_pages(original, STACK, page1.height - (STACK - 1) * STACK_STEP[1])
    save(before_after(manuscript, page1), outdir / "before-after.png")
    save(theme_sheet({theme: key_pages(theme, SHEET_PPI, *args) for theme in THEMES}), outdir / "themes.png")


if __name__ == "__main__":
    main()
