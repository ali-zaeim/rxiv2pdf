"""Figure normalisation: TIFF (or anything Typst can't embed) -> PNG; cap the long edge."""

from __future__ import annotations

import bisect
import io
import logging
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

log = logging.getLogger(__name__)

MAX_EDGE = 3000
_KEEP_FORMATS = {"JPEG": ".jpg", "PNG": ".png"}

_MAGIC_EXT = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
)
_CTYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/tiff": ".tif",
    "image/webp": ".webp",
}


def sniff_ext(head: bytes, content_type: str | None = None) -> str:
    for magic, ext in _MAGIC_EXT:
        if head.startswith(magic):
            return ext
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    ctype = (content_type or "").split(";")[0].strip().lower()
    return _CTYPE_EXT.get(ctype, ".bin")


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    format: str  # format of the file now on disk, e.g. "JPEG"
    original_format: str
    width: int
    height: int
    converted: bool  # re-encoded to PNG
    downscaled: bool


def normalize(path: Path, max_edge: int = MAX_EDGE) -> ImageInfo:
    """Make ``path`` embeddable: JPEG/PNG only, long edge <= ``max_edge``.

    Returns info about the resulting file, which may have a new extension (``.png``).
    The original file is removed if it was replaced.
    """
    # Decoded from memory: Pillow memory-maps uncompressed images (TIFF), and the map keeps
    # ``path`` open as long as ``im`` lives, so Windows could not delete it below.
    with Image.open(io.BytesIO(path.read_bytes())) as im:
        original_format = im.format or "UNKNOWN"
        im.load()
        needs_convert = original_format not in _KEEP_FORMATS
        needs_downscale = max(im.size) > max_edge
        if not needs_convert and not needs_downscale:
            return ImageInfo(path, original_format, original_format, im.width, im.height, False, False)

        out = im
        if needs_convert:
            out = _to_png_mode(out)
        if needs_downscale:
            if out is im:
                out = im.copy()
            out.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        fmt = "PNG" if needs_convert else original_format
        dest = path.with_suffix(_KEEP_FORMATS[fmt])
        tmp = dest.with_name(dest.name + ".tmp")
        save_kwargs = {"quality": 92, "optimize": True} if fmt == "JPEG" else {"optimize": True}
        if fmt == "JPEG" and out.mode not in ("RGB", "L"):
            out = out.convert("RGB")
        out.save(tmp, format=fmt, **save_kwargs)
        width, height = out.size

    tmp.replace(dest)
    if dest != path:
        path.unlink(missing_ok=True)
    log.debug(
        "Normalised %s (%s, converted=%s, downscaled=%s) -> %s %dx%d",
        path.name, original_format, needs_convert, needs_downscale, dest.name, width, height,
    )
    return ImageInfo(dest, fmt, original_format, width, height, needs_convert, needs_downscale)


def _to_png_mode(im: Image.Image) -> Image.Image:
    """Map modes PNG can't store (CMYK, 16-bit TIFF variants, palette+alpha...) onto ones it can."""
    if im.mode in ("RGB", "RGBA", "L", "LA", "P", "1"):
        return im
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        return im.convert("I").point(lambda v: v / 256).convert("L")
    if "A" in im.mode:
        return im.convert("RGBA")
    return im.convert("RGB")


def text_line_height(path: Path) -> float | None:
    """Median height in pixels of the lines of text in a table screenshot, or None if unclear.

    bioRxiv renders every table image 1280 px wide whatever the table, so the pixel width says
    nothing about the type size; the line height does. Rows of a table image alternate between
    ink (a line of text) and blank space. Rules are not text: columns dark over more than a
    third of the height (vertical rules, cell borders) are ignored, and rows more than half
    dark (horizontal rules, filled bands) count as blank.
    """
    try:
        with Image.open(path) as im:
            gray = im.convert("L")
    except (OSError, ValueError):
        return None
    w, h = gray.size
    if w < 50 or h < 20:
        return None
    ink = gray.point(lambda v: 255 if v < 140 else 0)
    col_ink = [c / 255 for c in _values(ink.convert("F").resize((w, 1), Image.Resampling.BOX))]
    keep = [255 if c < 0.35 else 0 for c in col_ink]
    kept = keep.count(255)
    if kept < w * 0.2:
        return None
    mask = Image.new("L", (w, 1))
    mask.putdata(keep)
    masked = ImageChops.multiply(ink, mask.resize((w, h)))
    rows = [r / 255 for r in _values(masked.convert("F").resize((1, h), Image.Resampling.BOX))]
    frac = [r * w / kept for r in rows]
    runs: list[int] = []
    start = None
    for y, f in enumerate([*frac, 0.0]):
        text = 0.003 < f < 0.5
        if text and start is None:
            start = y
        elif not text and start is not None:
            if y - start >= 4:
                runs.append(y - start)
            start = None
    return float(statistics.median(runs)) if len(runs) >= 3 else None


# Lettering of figures: median glyph height ~ 0.53 x the font size (measured on Typst-rendered
# tick labels and axis titles in serif, sans and mono type: 0.44-0.59).
GLYPH_PER_EM = 0.53
_INK_RUN = re.compile(rb"\x01+")


def lettering_height(path: Path) -> float | None:
    """Median height in pixels of the glyphs of a figure's lettering, or None if unclear.

    Like table screenshots, figures come ~1280 px wide whatever their content, so a simple
    chart printed at full width can have lettering at twice the body size. Lettering is dark
    and grey (data marks, heat-map cells and photos are mostly coloured or not glyph-shaped):
    connected components of dark, unsaturated ink that are glyph-shaped and sit next to a
    similar one on the same baseline (tick labels, axis titles, legends) are counted.
    """
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
    except (OSError, ValueError):
        return None
    w, h = rgb.size
    if w < 100 or h < 50:
        return None
    r, g, b = rgb.split()
    chroma = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b), ImageChops.darker(ImageChops.darker(r, g), b))
    dark = rgb.convert("L").point(lambda v: 255 if v < 110 else 0)
    grey = chroma.point(lambda v: 255 if v < 70 else 0)
    data = ImageChops.multiply(dark, grey).point(lambda v: 1 if v else 0).tobytes()

    # Connected components (8-connected) from the ink runs of each row, joined by union-find.
    parent: list[int] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs: list[tuple[int, int, int]] = []  # (x0, x1, y)
    prev: list[tuple[int, int, int]] = []  # (x0, x1, run id) on the row above
    for y in range(h):
        cur = []
        j = 0
        for m in _INK_RUN.finditer(data, y * w, (y + 1) * w):
            x0, x1 = m.start() - y * w, m.end() - y * w
            rid = len(runs)
            runs.append((x0, x1, y))
            parent.append(rid)
            while j < len(prev) and prev[j][1] < x0:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] <= x1:
                a, c = find(prev[k][2]), find(rid)
                if a != c:
                    parent[c] = a
                k += 1
            cur.append((x0, x1, rid))
        prev = cur
        if len(runs) > 400_000:  # a photograph or noise, not lettering
            return None
    comps: dict[int, list[int]] = {}
    for rid, (x0, x1, y) in enumerate(runs):
        box = comps.setdefault(find(rid), [x0, y, x1, y + 1, 0])
        box[0], box[1], box[2], box[3] = min(box[0], x0), min(box[1], y), max(box[2], x1), max(box[3], y + 1)
        box[4] += x1 - x0
    glyphs = sorted(
        (x0, y0, x1, y1)
        for x0, y0, x1, y1, n in comps.values()
        if 6 <= y1 - y0 <= 120 and 0.08 * (y1 - y0) <= x1 - x0 <= 1.6 * (y1 - y0) and 0.12 <= n / ((x1 - x0) * (y1 - y0)) <= 0.85
    )
    # Words: a glyph with a neighbour of similar height on the same baseline, close to its right.
    starts = [gl[0] for gl in glyphs]
    in_word: set[tuple[int, int, int, int]] = set()
    for a in glyphs:
        ah = a[3] - a[1]
        lo, hi = bisect.bisect_left(starts, a[2] - ah), bisect.bisect_right(starts, a[2] + 0.8 * ah)
        for c in glyphs[lo:hi]:
            ch = c[3] - c[1]
            if c is not a and c[0] >= a[0] + 0.5 * (a[2] - a[0]) and 0.55 < ch / ah < 1.8 and abs(a[3] - c[3]) < 0.35 * max(ah, ch):
                in_word.update((a, c))
    heights = [gl[3] - gl[1] for gl in in_word]
    return float(statistics.median(heights)) if len(heights) >= 12 else None


def _values(im: Image.Image) -> list[float]:
    """Pixel values of a single-band image (``getdata`` is deprecated from Pillow 12.1)."""
    flat = getattr(im, "get_flattened_data", None)
    return list(flat() if flat else im.getdata())
