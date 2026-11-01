"""Stage 4 - Render: ``Article`` -> Typst source that imports ``journal.typ``.

All styling lives in the template; this module only emits data and calls template
functions. Text is escaped for Typst markup (rules verified against the Typst 0.15
parser: every ASCII punctuation mark is a valid ``\\`` escape; ``//``/``/*`` open comments,
``-- --- ... ~`` are shorthands, and ``1. - + = /`` at line start create lists/headings).
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

from . import __version__
from .mathml import mathml_to_segments, mathml_to_typst, validate
from .model import (
    Article,
    Block,
    Cite,
    DefList,
    DisplayMath,
    Figure,
    Inline,
    InlineGraphic,
    InlineMath,
    LineBreak,
    Link,
    ListBlock,
    Paragraph,
    Preformatted,
    Quote,
    ReferenceListMarker,
    Section,
    Styled,
    Supplement,
    TableBlock,
    TableCell,
    TableData,
    Text,
    XRef,
    plain_text,
)

log = logging.getLogger(__name__)

TEMPLATE_NAME = "journal.typ"

# Markup-special characters (see module docstring). Quotes are left alone so Typst
# applies smart quotes; '.' is handled separately ("1." starts a list, "..." is a shorthand).
_MARKUP_SPECIAL = set("\\#$*_@<>`[]~/-=+")
_DIGIT_DOT = re.compile(r"(\d)\.")
_WS = re.compile(r"\s+")
_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_STYLE_FUNCS = {
    "bold": "strong", "italic": "emph", "sup": "super", "sub": "sub", "sc": "smallcaps",
    "mono": "mono", "underline": "underline", "strike": "strike", "overline": "overline",
}
# JATS list-type -> Typst enum numbering
_NUMBERING = {"alpha-lower": "a.", "alpha-upper": "A.", "roman-lower": "i.", "roman-upper": "I."}
_PAPER = {"letter": "us-letter", "us-letter": "us-letter", "a4": "a4"}

# Text area (width, height) in mm under journal.typ's margins (x 18mm, top/bottom 20mm);
# a test keeps these in sync with the template. Used only to estimate table lengths.
TEXT_AREA_MM = {"us-letter": (215.9 - 36, 279.4 - 40), "a4": (210.0 - 36, 297.0 - 40)}
DEFER_TABLE_PAGES = 2.0  # tables estimated longer than this go to the end of the article
_FLOAT_MAX = 0.94  # journal.typ theme `float-max`
_XML_ROW_MM = 4.6  # one 8pt table row incl. padding (rows that wrap make this an underestimate)
_TABLE_TYPE_MM = 8 * 25.4 / 72  # journal.typ `table-size` (default theme)


def escape(text: str) -> str:
    """Escape plain text for Typst markup. Whitespace is collapsed (no raw newlines)."""
    text = _WS.sub(" ", text)
    out = "".join("\\" + c if c in _MARKUP_SPECIAL else c for c in text)
    out = _DIGIT_DOT.sub(r"\1\\.", out)
    out = re.sub(r"\.(?=\.)", r"\\.", out)  # "..." would become an ellipsis
    # A text run right after an embedded call (#emph[..]) must not start with '.' or '('
    # or Typst would read it as a field access / argument list.
    if out[:1] in (".", "("):
        out = "\\" + out
    return out


# DNA/RNA sequences (primers, probes): hyphenation would insert "-" that reads as part of the
# sequence. They are set without it; ones too long to keep on a line (> 30 bases) may break,
# without a hyphen, every 10 bases. Protein sequences in one-letter code (seen: a 300-residue
# construct ran off the page) are any 25+ capitals in a row: longer than any word or acronym.
_SEQUENCE = re.compile(
    r"(?<![A-Za-z])[ACGTUN]{12,}(?![A-Za-z])|(?<![A-Za-z])[acgtun]{12,}(?![A-Za-z])|(?<![A-Za-z])[A-Z]{25,}(?![A-Za-z])"
)
_SEQ_BREAK = 10
# Dirac kets "|R⟩": no line break after the "|".
_KET_BAR = re.compile(r"\|(?=[^\s|]|$)")  # also "|" ending a run: "|<italic>R</italic>⟩"


def _text(text: str) -> str:
    """Escape a text run, protecting sequences from hyphenation and kets from breaking."""
    out, pos = [], 0
    for m in _SEQUENCE.finditer(text):
        out.append(_escape_ket(text[pos : m.start()]))
        seq = m.group(0)
        if len(seq) > 3 * _SEQ_BREAK:
            seq = "\u200b".join(seq[i : i + _SEQ_BREAK] for i in range(0, len(seq), _SEQ_BREAK))
        out.append(f"#text(hyphenate: false)[{seq}]")
        pos = m.end()
    out.append(_escape_ket(text[pos:]))
    return "".join(out)


def _escape_ket(text: str) -> str:
    return _KET_BAR.sub("|\u2060", escape(text)) if "|" in text else escape(text)


def string(value: str) -> str:
    """A Typst string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "") + '"'


def collapse_refs(pairs: list[tuple[int, str]]) -> list[tuple[int, int, str, str]]:
    """[(1,'c1'),(2,'c2'),(3,'c3'),(7,'c7')] -> [(1, 3, 'c1', 'c3'), (7, 7, 'c7', 'c7')]."""
    segments: list[list] = []
    for n, rid in pairs:
        if segments and n == segments[-1][1] + 1:
            segments[-1][1], segments[-1][3] = n, rid
        elif not segments or not (segments[-1][0] <= n <= segments[-1][1]):
            segments.append([n, n, rid, rid])
    return [tuple(s) for s in segments]  # type: ignore[misc]


def collapse(numbers: Iterable[int]) -> list[tuple[int, int]]:
    """[1,2,3,4,7,9,10] -> [(1,4), (7,7), (9,10)]. Keeps first-occurrence order of runs."""
    segments: list[tuple[int, int]] = []
    for n in numbers:
        if segments and n == segments[-1][1] + 1:
            segments[-1] = (segments[-1][0], n)
        elif not segments or not (segments[-1][0] <= n <= segments[-1][1]):
            segments.append((n, n))
    return segments


def license_name(code: str | None, url: str | None) -> str | None:
    """'http://creativecommons.org/licenses/by-nc-nd/4.0/' -> 'CC BY-NC-ND 4.0'."""
    if url:
        m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d\.\d)", url)
        if m:
            return f"CC {m.group(1).upper()} {m.group(2)}"
        if "publicdomain/zero" in url:
            return "CC0 1.0"
    names = {
        "cc_by": "CC BY 4.0", "cc_by_nc": "CC BY-NC 4.0", "cc_by_nd": "CC BY-ND 4.0",
        "cc_by_nc_nd": "CC BY-NC-ND 4.0", "cc0": "CC0 1.0", "cc_no": "No reuse allowed without permission",
    }
    return names.get(code or "", code or None)


@dataclass(frozen=True)
class ImageRef:
    """A downloaded graphic: path relative to the .typ, plus pixel size (for formula images)
    and the size of its text in pixels, to size it by: for table screenshots the height of
    their text lines, for figures the median height of their lettering's glyphs."""

    path: str
    width: int | None = None
    height: int | None = None
    text_px: float | None = None


@dataclass(frozen=True)
class AppendixItem:
    """A supplementary file appended after the article (``--append-supplements``)."""

    label: str
    file: str  # file name, as listed in the article
    path: str  # relative to the .typ
    pages: int  # pages to place (1 for an image)
    href: str  # JATS media href, to link the article's listing to it


THEMES = ("default", "compact", "sans")


@dataclass
class RenderOptions:
    paper: str = "letter"
    theme: str = "default"  # templates/themes/<theme>.typ
    cite_style: str | None = None  # override the theme's citation style ("superscript" | "brackets")


class _Renderer:
    def __init__(
        self, article: Article, assets: dict[str, ImageRef], options: RenderOptions,
        supplement_urls: dict[str, str], appendix: list[AppendixItem],
    ):
        self.a = article
        self.assets = assets
        self.options = options
        self.supplement_urls = supplement_urls
        self.appendix = appendix
        self.ref_num = {r.id: r.number for r in article.references}
        # Labels come from the (unique) XML id, not the number: numbering restarts per list.
        self.ref_label: dict[str, str] = {}
        for i, r in enumerate(article.references):
            lbl = "ref-" + _LABEL_UNSAFE.sub("_", r.id)
            self.ref_label[r.id] = lbl if lbl not in self.ref_label.values() else f"{lbl}-{i}"
        self.labels: dict[str, str] = {}  # JATS id -> Typst label name
        self.emitted: set[str] = set()
        self.warnings: list[str] = []
        # Pre-pass: exactly the blocks that will carry a label (links to anything else
        # would be a Typst compile error, so XRefs to them degrade to plain text).
        abstract_inner = [x for b in article.abstract for x in (b.blocks if isinstance(b, Section) else [b])]
        self.math = self._convert_all_math()  # MathML string -> validated Typst, or None
        self.after: dict[int, list[Block]] = {}  # id(paragraph) -> floats to emit after it
        self.moved: set[int] = set()
        self.deferred: list[TableBlock] = []  # long tables, emitted at the end of the article
        self._plan_float_placement()
        for block in _walk(abstract_inner + article.body + article.back):
            prefix = {Figure: "fig", TableBlock: "tab", DisplayMath: "eq", Section: "sec", Supplement: "supp"}.get(type(block))
            block_id = getattr(block, "id", None)
            if isinstance(block, Section) and not block.title:
                continue
            if prefix and block_id and block_id not in self.labels:
                self.labels[block_id] = f"{prefix}-{_LABEL_UNSAFE.sub('_', block_id)}"

    def _convert_all_math(self) -> dict[str, str | None]:
        found: list[str] = []

        def scan_inlines(nodes: list[Inline]) -> None:
            for n in nodes:
                if isinstance(n, InlineMath) and n.mathml:
                    found.append(n.mathml)
                if isinstance(n, Styled | Link | XRef):
                    scan_inlines(n.children)

        for b in _walk(self.a.abstract + self.a.body + self.a.back):
            if isinstance(b, DisplayMath) and b.mathml:
                found.append(b.mathml)
            for attr in ("content", "title"):
                scan_inlines(getattr(b, attr, None) or [])
        for r in self.a.references:
            scan_inlines(r.content)
        unique = list(dict.fromkeys(found))
        converted = {m: mathml_to_typst(m) for m in unique}
        candidates = [m for m in unique if converted[m]]
        for m, good in zip(candidates, validate([converted[m] for m in candidates]), strict=True):
            if not good:
                converted[m] = None
                self.warnings.append("A MathML formula Typst could not typeset was shown as its image/text instead")
        return converted

    def _plan_float_placement(self) -> None:
        """Place each figure/table after its first citation. Body floats move to just
        after the paragraph that first cites them (captions don't count); uncited ones and
        back-matter floats (Extended Data) keep their source position."""
        first: dict[str, Paragraph] = {}

        def scan(nodes: list[Inline], para: Paragraph) -> None:
            for n in nodes:
                if isinstance(n, XRef) and n.kind in ("fig", "table"):
                    first.setdefault(n.target, para)
                if isinstance(n, Styled | Link | XRef):
                    scan(n.children, para)

        floats: list[Block] = []
        order: dict[int, int] = {}  # id(paragraph) -> position in the text
        for b in _walk_flow(self.a.body):
            if isinstance(b, Paragraph):
                order[id(b)] = len(order)
                scan(b.content, b)
            elif isinstance(b, Figure | TableBlock):
                floats.append(b)
        # Figures (and tables) stay in number order: one first cited before its predecessor
        # ("see Figure 5" in the Methods) waits for that predecessor instead of jumping ahead.
        previous: dict[tuple[type, bool], Paragraph] = {}
        for f in floats:
            para = first.get(f.id or "")
            if para is not None:
                key = (type(f), bool(getattr(f, "supplementary", False)))
                if key in previous and order[id(previous[key])] > order[id(para)]:
                    para = previous[key]
                previous[key] = para
                self.after.setdefault(id(para), []).append(f)
                self.moved.add(id(f))
            if isinstance(f, TableBlock) and self.table_pages(f) > DEFER_TABLE_PAGES:
                self.deferred.append(f)

    def table_pages(self, t: TableBlock) -> float:
        """Rough page count a table would occupy (0 when its size is unknown)."""
        text_w, text_h = TEXT_AREA_MM[_PAPER.get(self.options.paper, self.options.paper)]
        if t.table is not None and not (t.table.malformed and t.graphics):
            rows = len(t.table.head) + len(t.table.body) + len(t.table.foot)
            return rows * _XML_ROW_MM / text_h  # full-width chunks, one page each
        refs = [r for href in t.graphics if (r := self.assets.get(href)) and r.width and r.height]
        if not refs:
            return 0.0
        # Same scale as journal.typ's table-block: type at the table size, at most full width.
        scale = text_w / max(r.width for r in refs)  # mm per px
        lines = sorted(r.text_px for r in refs if r.text_px)
        if len(lines) == len(refs):
            scale = min(scale, 0.9 * _TABLE_TYPE_MM / lines[len(lines) // 2])
        return sum(min(r.height * scale, _FLOAT_MAX * text_h) for r in refs) / text_h

    # -- inline -----------------------------------------------------------------------------
    def inline(self, nodes: list[Inline]) -> str:
        return "".join(self._inline(n) for n in nodes)

    def _inline(self, n: Inline) -> str:
        if isinstance(n, Text):
            return _text(n.text)
        if isinstance(n, Styled):
            body = self.inline(n.children)
            return f"#{_STYLE_FUNCS[n.style]}[{body}]" if body else ""
        if isinstance(n, Link):
            return f"#link({string(n.url)})[{self.inline(n.children) or escape(n.url)}]"
        if isinstance(n, Cite):
            pairs = [(self.ref_num[rid], rid) for rid in n.ref_ids if rid in self.ref_num]
            if not pairs:
                return "\\[?\\]"
            segs = ", ".join(
                f"({a}, {b}, {string(self.ref_label[ra])}, {string(self.ref_label[rb])})"
                for a, b, ra, rb in collapse_refs(pairs)
            )
            return f"#cites(({segs},))"
        if isinstance(n, XRef):
            body = self.inline(n.children)
            if n.kind == "bibr" and n.target in self.ref_label:
                return f"#link(label({string(self.ref_label[n.target])}))[{body}]"
            lbl = self.labels.get(n.target)
            return f"#link(label({string(lbl)}))[{body}]" if lbl else body
        if isinstance(n, InlineMath):
            # Preference: MathML -> typeset math; else the provided image; else TeX
            # source; else plain text. (Every formula sampled on bioRxiv was an image.)
            if n.mathml and (math := self.math.get(n.mathml)):
                return f"${math}$"
            if n.image and n.image in self.assets:
                return self._inline_image(self.assets[n.image])
            if n.tex:
                return f"#raw({string(n.tex)})"
            return escape(n.text)
        if isinstance(n, InlineGraphic):
            return self._inline_image(self.assets[n.href]) if n.href in self.assets else ""
        if isinstance(n, LineBreak):
            return "#linebreak()"
        return ""

    def _inline_image(self, ref: ImageRef) -> str:
        return f"#inline-image({string(ref.path)}, px-height: {ref.height or 'none'}, px-width: {ref.width or 'none'})"

    def content(self, nodes: list[Inline]) -> str:
        return f"[{self.inline(nodes)}]"

    # -- blocks -----------------------------------------------------------------------------
    def blocks(self, blocks: list[Block], depth: int) -> str:
        return "".join(self.block(b, depth) for b in blocks)

    def _label(self, block_id: str | None) -> str:
        lbl = self.labels.get(block_id or "")
        if not lbl or lbl in self.emitted:
            return ""
        self.emitted.add(lbl)
        return f" <{lbl}>"

    def _images(self, hrefs: list[str], what: str) -> str:
        paths = []
        for href in hrefs:
            if href in self.assets:
                paths.append(string(self.assets[href].path))
            else:
                self.warnings.append(f"{what}: image {href} is missing; rendering a placeholder")
        return "(" + "".join(p + ", " for p in paths) + ")"

    def block(self, b: Block, depth: int) -> str:
        if isinstance(b, Paragraph):
            text = self.inline(b.content)
            if not text.strip():
                return ""
            out = f"#cont-par[{text}]\n\n" if b.continues else f"{text}\n\n"
            for f in self.after.get(id(b), []):
                out += self._float_block(f, depth)
            return out
        if isinstance(b, Section):
            if not _has_content(b):
                return ""  # e.g. bioRxiv's empty <ack><title>Acknowledgments</title></ack>
            if self._floats_only(b):
                # Only floats ("Supplementary tables"): they go to the tops of pages and would
                # leave the heading alone in the text, so it goes with the first of them.
                return self._float_section(b, depth, "")[0]
            title = self.inline(b.title)
            head = f"{'=' * min(depth, 6)} {title}{self._label(b.id)}\n\n" if title.strip() else ""
            first = b.blocks[0] if b.blocks else None
            if head and depth >= 2 and isinstance(first, Paragraph) and not first.continues:
                # No paragraph break: lets a theme set subsection titles run-in ("Title. Text…").
                head = head[:-1]
            return head + self.blocks(b.blocks, depth + 1)
        if isinstance(b, Figure | TableBlock):
            return "" if id(b) in self.moved else self._float_block(b, depth)
        return self._block_rest(b, depth)

    def _block_rest(self, b: Block, depth: int) -> str:
        if isinstance(b, DisplayMath):
            if b.mathml and (math := self.math.get(b.mathml)):
                segs = mathml_to_segments(b.mathml) or []
                seg_arg = ", ".join(string(s) for s in segs) + ("," if len(segs) == 1 else "")
                body = f"#display-math(${' '}{math}{' '}$, segments: ({seg_arg}))"
            elif b.image and b.image in self.assets:
                ref = self.assets[b.image]
                body = f"#display-image({string(ref.path)}, px-width: {ref.width or 'none'})"
            elif b.tex:
                body = f"#display-text(raw({string(b.tex)}))"
            else:
                body = f"#display-text[{escape(b.text)}]"
            return f"{body}{self._label(b.id)}\n\n"
        if isinstance(b, ListBlock):
            func = "enum" if b.ordered else "list"
            items = ", ".join(f"[{self.blocks(item, depth).strip()}]" for item in b.items)
            numbering = f"numbering: {string(_NUMBERING[b.style])}, " if b.ordered and b.style in _NUMBERING else ""
            return f"#{func}({numbering}{items}{',' if len(b.items) == 1 else ''})\n\n"
        if isinstance(b, DefList):
            items = ", ".join(f"({self.content(i.term)}, [{self.blocks(i.definition, depth).strip()}])" for i in b.items)
            return f"#def-list({items})\n\n" if b.items else ""
        if isinstance(b, Quote):
            return f"#quote(block: true)[{self.blocks(b.blocks, depth).strip()}]\n\n"
        if isinstance(b, Preformatted):
            return f"#raw({string(b.text)}, block: true)\n\n"
        if isinstance(b, Supplement):
            file = string(b.href.rsplit("/", 1)[-1]) if b.href else "none"
            url = self.supplement_urls.get(b.href or "")
            appended = next((i for i, x in enumerate(self.appendix) if x.href == b.href), None)
            return (
                f"#supplement-item(lbl: {self._opt(b.label)}, file: {file}, url: {string(url) if url else 'none'}, "
                f"appended: {appended if appended is not None else 'none'}, "
                f"caption: [{self.blocks(b.caption, depth).strip()}]){self._label(b.id)}\n\n"
            )
        if isinstance(b, ReferenceListMarker):
            return self.references(b, depth)
        return ""

    def _floats_only(self, section: Section) -> bool:
        """The section is nothing but floats that stay in it (not moved to a citation, not a
        table deferred to the end), possibly in subsections ("Supplementary information" >
        "Supplementary figures", "Supplementary tables")."""
        items = [x for x in section.blocks if not (isinstance(x, Section) and not _has_content(x))]
        return bool(items) and all(
            self._floats_only(x) if isinstance(x, Section)
            else isinstance(x, Figure | TableBlock) and id(x) not in self.moved and not any(x is t for t in self.deferred)
            for x in items
        )

    def _float_section(self, section: Section, depth: int, pending: str) -> tuple[str, str]:
        """Render a floats-only section: its heading (and those of the sections it opens, in
        `pending`) goes into the first float below it. Returns (markup, headings not placed)."""
        title = self.inline(section.title)
        if title.strip():
            pending += f"{'=' * min(depth, 6)} {title}{self._label(section.id)}\n"
        out = ""
        for x in section.blocks:
            if isinstance(x, Section):
                if _has_content(x):
                    markup, pending = self._float_section(x, depth + 1, pending)
                    out += markup
            elif isinstance(x, Figure | TableBlock):
                out += self._float_block(x, depth + 1, header=pending.strip() or None)
                pending = ""
        return out, pending

    def _float_block(self, b: Figure | TableBlock, depth: int, *, at_end: bool = False, header: str | None = None) -> str:
        if isinstance(b, TableBlock) and any(b is t for t in self.deferred) and not at_end:
            target = string(self.labels[b.id]) if b.id in self.labels else "none"
            pages = max(3, round(self.table_pages(b)))
            return f"#deferred-note(lbl: {self._opt(b.label)}, target: {target}, pages: {pages})\n\n"
        caption = self.blocks(_run_in(b.caption), depth).strip()
        hdr = f", header: [{header}]" if header else ""
        words = len(plain_text(b.title).split()) + sum(
            len(plain_text(p.content).split()) for p in _walk_flow(b.caption) if isinstance(p, Paragraph)
        )
        if isinstance(b, Figure):
            return (
                f"#figure-block(lbl: {self._opt(b.label)}, title: {self.content(b.title)}, caption: [{caption}], "
                f"images: {self._images(b.graphics, b.label or 'Figure')}, sizes: {self._sizes(b.graphics)}, "
                f"aspect: {self._aspect(b.graphics)}, "
                f"caption-words: {words}, supplementary: {_bool(b.supplementary)}{hdr}){self._label(b.id)}\n\n"
            )
        xml = "none"
        if b.table is not None and (not b.table.malformed or not b.graphics):
            xml = self._xml_table(b.table)
        images = "()" if xml != "none" else self._images(b.graphics, b.label or "Table")
        sizes = "()" if xml != "none" else self._sizes(b.graphics)
        note = "none"
        if xml == "none" and not b.graphics:
            note = string("Table")
        return (
            f"#table-block(lbl: {self._opt(b.label)}, title: {self.content(b.title)}, caption: [{caption}], "
            f"images: {images}, sizes: {sizes}, aspect: {self._aspect(b.graphics) if xml == 'none' else 'none'}, "
            f"footer: [{self.blocks(b.footer, depth).strip()}], note: {note}, xml: {xml}{hdr}){self._label(b.id)}\n\n"
        )

    def _sizes(self, hrefs: list[str]) -> str:
        """(px-width, px-height, text-px) of each present image, aligned with ``_images``;
        `none` entries when the pixel size is unknown."""
        out = []
        for href in hrefs:
            if (r := self.assets.get(href)) is None:
                continue
            text = "none" if r.text_px is None else f"{r.text_px:g}"
            out.append(f"({r.width}, {r.height}, {text})" if r.width and r.height else "none")
        return "(" + "".join(x + ", " for x in out) + ")"

    def _aspect(self, hrefs: list[str]) -> str:
        """Width/height of the stacked images (from downloaded pixel sizes), or none."""
        dims = [(r.width, r.height) for h in hrefs if (r := self.assets.get(h)) and r.width and r.height]
        if not dims:
            return "none"
        return f"{max(w for w, _ in dims) / sum(h for _, h in dims):.3f}"

    def _xml_table(self, t: TableData) -> str:
        def cell(c: TableCell) -> str:
            args = []
            if c.colspan > 1:
                args.append(f"colspan: {c.colspan}")
            if c.rowspan > 1:
                args.append(f"rowspan: {c.rowspan}")
            if c.align in ("left", "center", "right"):
                args.append(f"align: {c.align}")
            body = f"[{self.inline(c.content)}]"
            return f"table.cell({', '.join(args)}){body}" if args else body

        def rows(rs: list[list[TableCell]]) -> str:
            cells = [cell(c) for r in rs for c in r]
            return "(" + "".join(c + ", " for c in cells) + ")"

        groups = "".join(rows(g) + ", " for g in _row_groups(t.body))
        return (
            f"(ncols: {t.ncols}, nhead: {len(t.head)}, head: {rows(t.head)}, "
            f"groups: ({groups}), foot: {rows(t.foot)})"
        )

    def _opt(self, text: str | None) -> str:
        return f"[{escape(text)}]" if text else "none"

    def references(self, marker: ReferenceListMarker, depth: int) -> str:
        out = [f"{'=' * depth} {self.inline(marker.title)}\n\n#references-list[\n"]
        numbered = self.a.cite_mode == "numeric"  # author-year lists are alphabetical, unnumbered
        mine = set(marker.ref_ids)
        for r in self.a.references:
            if mine and r.id not in mine:
                continue  # belongs to another reference list (e.g. supplementary references)
            num = r.number if numbered else "none"
            out.append(f"#ref-entry({num}, [{self.inline(r.content)}]) <{self.ref_label[r.id]}>\n")
        return "".join(out) + "]\n\n"

    # -- document ---------------------------------------------------------------------------
    def document(self) -> str:
        a = self.a
        # Figures/tables after all the text go on pages of their own (journal.typ `tail`).
        kept, trailing = self._split_tail(a.back, 1)
        body = self.blocks(a.body, 1)
        back = self.blocks(kept, 1)
        tail = "".join(self.blocks(blocks, depth) for blocks, depth in trailing)
        tail += "".join(self._float_block(t, 1, at_end=True) for t in self.deferred)
        tail_arg = f"[#back-matter[\n{tail}]]" if tail.strip() else "none"
        aff_index = {aff.id: i for i, aff in enumerate(a.affiliations, 1)}
        authors = []
        for au in a.authors:
            affs = ", ".join(str(aff_index[x]) for x in au.aff_ids if x in aff_index)
            authors.append(
                f"    (name: [{escape(au.display_name)}], name-text: {string(au.display_name)}, "
                f"affs: ({affs}{',' if affs and ',' not in affs else ''}), equal: {_bool(au.equal_contrib)}, "
                f"corresponding: {_bool(au.corresponding)}, orcid: {string(au.orcid) if au.orcid else 'none'}, "
                f"notes: ({''.join(string(m) + ', ' for m in au.note_marks)})),"
            )
        affiliations = "".join(f"    {self.content(x.content)},\n" for x in a.affiliations)
        corresp = "".join(f"{self.content(c)}, " for c in a.corresp)
        author_notes = "".join(f"({string(m)}, {self.content(c)}), " for m, c in a.author_notes)
        abstract = self._abstract()
        keywords = "".join(f"{string(k)}, " for k in a.keywords)
        lic = license_name(a.license_code, a.license_url)

        header = f"""// Generated by rxiv2pdf {__version__} from {a.doi or 'unknown DOI'}{f' v{a.version}' if a.version else ''}.
// Styling lives in {TEMPLATE_NAME}; this file only carries the article's content.
#import "{TEMPLATE_NAME}": *
#import "themes/{self.options.theme}.typ": theme as rxiv-theme

#show: article.with(
  title: {self.content(a.title)},
  title-text: {string(plain_text(a.title))},
  short-title: {string(short_title(plain_text(a.title)))},
  running-author: {string(running_author(a)) if a.authors else 'none'},
  authors: (
{chr(10).join(authors)}
  ),
  affiliations: (
{affiliations}  ),
  corresp: ({corresp}),
  equal-note: {self.content(a.equal_contrib_note) if a.equal_contrib_note else 'none'},
  author-notes: ({author_notes}),
  abstract: {abstract},
  keywords: ({keywords}),
  doi: {string(a.doi) if a.doi else 'none'},
  server: {string(a.server or 'bioRxiv')},
  version: {a.version if a.version else 'none'},
  posted: {string(a.posted) if a.posted else 'none'},
  category: {string(a.category) if a.category else 'none'},
  license: {string(lic) if lic else 'none'},
  license-url: {string(a.license_url) if a.license_url else 'none'},
  withdrawn: {self._withdrawn()},
  section-numbering: {_bool(not a.source_numbered)},
  paper: {string(_PAPER.get(self.options.paper, self.options.paper))},
  cite-style: {string(self.options.cite_style) if self.options.cite_style else 'none'},
  theme: rxiv-theme,
  appendix: ({self._appendix()}),
  tail: {tail_arg},
)

"""
        return header + body + (f"#back-matter[\n{back}]\n" if back.strip() else "")

    def _split_tail(self, blocks: list[Block], depth: int) -> tuple[list[Block], list[tuple[list[Block], int]]]:
        """(blocks without the trailing floats, [(trailing blocks, heading depth)]). When the
        last section ends in them ("S2 Supplement" > "S2.2 Figures"), they are looked for in it."""
        start = self._tail_start(blocks)
        if start < len(blocks) or not blocks or not isinstance(blocks[-1], Section):
            return blocks[:start], ([(blocks[start:], depth)] if start < len(blocks) else [])
        last = blocks[-1]
        inner, trailing = self._split_tail(last.blocks, depth + 1)
        if not trailing:
            return blocks, []
        return [*blocks[:-1], dataclasses.replace(last, blocks=inner)], trailing

    def _tail_start(self, blocks: list[Block]) -> int:
        """Index where the trailing run of floats begins: figures/tables (in place, not moved
        to a citation or deferred) and sections holding only those, with no text after them."""
        i = len(blocks)
        while i > 0:
            b = blocks[i - 1]
            if isinstance(b, Figure | TableBlock):
                if id(b) in self.moved or any(b is t for t in self.deferred):
                    break
            elif isinstance(b, Section):
                if _has_content(b) and not self._floats_only(b):
                    break
            elif not (isinstance(b, Paragraph) and not plain_text(b.content).strip()):
                break
            i -= 1
        return i

    def _appendix(self) -> str:
        return "".join(
            f"(label: [{escape(x.label)}], file: {string(x.file)}, path: {string(x.path)}, pages: {x.pages}, "
            f"pdf: {_bool(x.path.lower().endswith('.pdf'))}), "
            for x in self.appendix
        )

    def _withdrawn(self) -> str:
        w = self.a.withdrawn
        if not w:
            return "none"
        return f"(date: {string(str(w.get('date', '')))}, version: {int(w.get('version', 0))}, statement: [{escape(str(w.get('statement', '')))}])"

    def _abstract(self) -> str:
        if not self.a.abstract:
            return "none"
        parts = []
        for b in self.a.abstract:
            if isinstance(b, Section) and b.title:
                inner = self.blocks(b.blocks, 2).strip()
                parts.append(f"#abstract-section({self.content(b.title)})[{inner}]\n\n")
            else:
                parts.append(self.block(b, 2))
        return "[" + "".join(parts).strip() + "]"


def short_title(title: str, limit: int = 60) -> str:
    """Truncate at a word boundary to about ``limit`` characters, adding an ellipsis."""
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    cut = title[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:–—-")
    return cut + "…"


def running_author(article: Article) -> str:
    """'Howard et al.' (or the sole author's surname / consortium name)."""
    first = article.authors[0]
    name = first.collab or first.surname or first.display_name
    return name if len(article.authors) == 1 else f"{name} et al."


def _bool(v: bool) -> str:
    return "true" if v else "false"


def _row_groups(rows: list[list[TableCell]]) -> list[list[list[TableCell]]]:
    """Consecutive rows that must stay together because a rowspan ties them; a long table
    is only ever split between groups."""
    groups: list[list[list[TableCell]]] = []
    reach = -1  # last row index covered by a rowspan in the current group
    for i, row in enumerate(rows):
        if i > reach:
            groups.append([])
        groups[-1].append(row)
        reach = max(reach, i, *(i + c.rowspan - 1 for c in row))
    return groups


def _run_in(blocks: list[Block]) -> list[Block]:
    """Join consecutive caption paragraphs into one, journal-style. bioRxiv XML often has one
    <p> per panel ("(A) ...", "(B) ..."; 41 of 325 sampled captions), which would otherwise
    start every panel on a new line."""
    out: list[Block] = []
    for b in blocks:
        if isinstance(b, Paragraph) and out and isinstance(out[-1], Paragraph):
            # "W: Wake" + "Note: ..." must not read "W: Wake Note: ..."
            ended = re.search(r"[.!?:;]['\"’”)\]]*\s*$", plain_text(out[-1].content))
            out[-1] = Paragraph([*out[-1].content, Text(" " if ended else ". "), *b.content], out[-1].continues)
        else:
            out.append(b)
    return out


def _has_content(section: Section) -> bool:
    return any(not isinstance(b, Section) or _has_content(b) for b in section.blocks)


def _walk_flow(blocks: list[Block]) -> Iterable[Block]:
    """Document-order walk of the text flow (sections, lists, quotes), not into captions."""
    for b in blocks:
        yield b
        if isinstance(b, Section | Quote):
            yield from _walk_flow(b.blocks)
        elif isinstance(b, ListBlock):
            for item in b.items:
                yield from _walk_flow(item)


def _walk(blocks: list[Block]) -> Iterable[Block]:
    for b in blocks:
        yield b
        if isinstance(b, Section | Quote):
            yield from _walk(b.blocks)
        elif isinstance(b, ListBlock):
            for item in b.items:
                yield from _walk(item)
        elif isinstance(b, Figure | TableBlock):
            yield from _walk(b.caption)


def render(
    article: Article,
    *,
    assets: dict[str, ImageRef],
    options: RenderOptions | None = None,
    supplement_urls: dict[str, str] | None = None,
    appendix: list[AppendixItem] | None = None,
) -> tuple[str, list[str]]:
    """Return (Typst source, render warnings). ``assets`` maps JATS hrefs to downloaded images;
    ``supplement_urls`` maps supplementary-file hrefs to where the server serves them;
    ``appendix`` lists supplementary files to append after the article."""
    options = options or RenderOptions()
    if options.theme not in THEMES:
        raise ValueError(f"Unknown theme {options.theme!r}; expected one of: {', '.join(THEMES)}")
    r = _Renderer(article, assets, options, supplement_urls or {}, appendix or [])
    return r.document(), r.warnings
