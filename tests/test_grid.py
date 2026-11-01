"""Baseline grid: lines in both columns share baselines and columns end on the same line.

Measured on the rendered page: each text line's baseline is the lowest row with >= 45% of
its band's peak ink (descenders are sparse). Before the grid, 46-101 lines per page had no
partner baseline in the other column; with it, single digits (headings, captions).
"""

import io

import pytest
import typst
from PIL import Image
from test_render import FIXTURES, compile_article

from rxiv2pdf.compile import font_dirs
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.render_typst import RenderOptions

PPI = 100
# Text-area geometry per theme, in mm (templates/themes/*.typ): side margin, column gutter,
# top margin, bottom margin.
GEOMETRY = {"default": (18, 5, 20, 20), "compact": (13, 4.5, 16, 15), "sans": (19, 7, 22, 20)}


def _baselines(im, x0, x1, y0, y1):
    px = im.load()
    ink = [sum(1 for x in range(x0, x1, 2) if px[x, y] < 110) for y in range(y0, y1)]
    # Lines are split at rows with little ink. On tight leading (compact) descenders touch the
    # next line's ascenders, so "little" is relative to a typical x-height row.
    gap = max(3, 0.1 * sorted(ink)[int(0.9 * len(ink))])
    out, start = [], None
    for i, n in enumerate(ink + [0]):
        if n > gap and start is None:
            start = i
        if n <= gap and start is not None:
            seg = ink[start:i]
            peak = max(seg)
            if 4 <= i - start <= 20:
                out.append(y0 + start + max(j for j, v in enumerate(seg) if v >= 0.45 * peak))
            start = None
    return out


def measure(png: bytes, theme: str = "default"):
    im = Image.open(io.BytesIO(png)).convert("L")
    w, h = im.size
    mm = lambda x: round(x / 25.4 * PPI)
    side, gutter, top, bottom = GEOMETRY[theme]
    m, g = mm(side), mm(gutter)
    cw = (w - 2 * m - g) // 2
    top, bottom = mm(top + 8), h - mm(bottom)  # below the running header, down to the text bottom
    left = _baselines(im, m, m + cw, top, bottom)
    right = _baselines(im, m + cw + g, w - m, top, bottom)
    unmatched = sum(min(abs(y - z) for z in right) > 1 for y in left) + sum(min(abs(y - z) for z in left) > 1 for y in right)
    return len(left), len(right), unmatched, left[-1] - right[-1]


@pytest.mark.parametrize("theme", ["default", "compact", "sans"])
def test_columns_share_baselines_and_end_together(theme, tmp_path):
    article = parse_jats(next(FIXTURES.glob("2026.10.16.759679/*.xml")).read_bytes())
    compile_article(article, tmp_path, options=RenderOptions(theme=theme))
    pages = typst.compile(str(tmp_path / "doc.typ"), root=str(tmp_path), format="png", ppi=PPI,
                          font_paths=font_dirs(), ignore_system_fonts=True)
    results = [measure(p, theme) for p in pages[1:-1]]  # not page 1 (front matter) or the last
    print(theme, results)
    for n_left, n_right, unmatched, _ in results[:4]:  # body text (later pages hold the references)
        assert unmatched <= 0.15 * (n_left + n_right)
    # Most pages end both columns on the same line. Not all: a heading that would land at the
    # foot of a column moves to the next one with its first paragraph, leaving the column short.
    assert sum(abs(d) <= 1 for *_, d in results) >= 2 / 3 * len(results)
