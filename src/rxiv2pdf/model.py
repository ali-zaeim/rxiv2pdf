"""Stage 3 output: the internal ``Article`` model (plain dataclasses).

The model is a pure function of the JATS XML (plus a little API metadata); it knows
nothing about Typst, the cache layout or downloaded files. Graphics are referenced by
their JATS ``xlink:href``; the renderer maps hrefs to local files.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------------------
# Inline content
# ---------------------------------------------------------------------------------------
Style = Literal["bold", "italic", "sup", "sub", "sc", "mono", "underline", "strike", "overline"]


@dataclass
class Text:
    text: str


@dataclass
class Styled:
    style: Style
    children: list[Inline]


@dataclass
class Link:
    url: str
    children: list[Inline]


@dataclass
class Cite:
    """A numeric citation group, e.g. JATS ``<sup><xref>1</xref>–<xref>5</xref></sup>``.

    ``ref_ids`` is the fully expanded list (ranges already expanded), in source order.
    The renderer maps ids to numbers and collapses runs ("3–6").
    """

    ref_ids: list[str]


@dataclass
class XRef:
    """Internal cross-reference that keeps its source text (``Fig. 1A``, ``Smith et al., 2020``)."""

    kind: str  # fig | table | bibr | sec | disp-formula | supplementary-material | ...
    target: str  # @rid
    children: list[Inline]


@dataclass
class InlineMath:
    text: str  # plain-text fallback
    tex: str | None = None
    mathml: str | None = None
    image: str | None = None  # graphic href


@dataclass
class InlineGraphic:
    href: str


@dataclass
class LineBreak:
    pass


Inline = Text | Styled | Link | Cite | XRef | InlineMath | InlineGraphic | LineBreak


# ---------------------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------------------
@dataclass
class Paragraph:
    content: list[Inline]
    continues: bool = False  # resumes a paragraph that a display element interrupted


@dataclass
class ListBlock:
    ordered: bool
    items: list[list[Block]]
    style: str | None = None  # JATS list-type when it sets the numbering ("alpha-lower", ...)


@dataclass
class DefItem:
    term: list[Inline]
    definition: list[Block]


@dataclass
class DefList:
    items: list[DefItem]


@dataclass
class Quote:
    blocks: list[Block]


@dataclass
class Preformatted:
    text: str


@dataclass
class DisplayMath:
    id: str | None
    label: str | None
    text: str
    tex: str | None = None
    mathml: str | None = None
    image: str | None = None  # graphic href


@dataclass
class Figure:
    id: str | None  # @id, the target of xref/@rid
    label: str | None  # "Figure 1" (trailing punctuation stripped)
    title: list[Inline]
    caption: list[Block]
    graphics: list[str]  # hrefs
    supplementary: bool = False


@dataclass
class TableCell:
    content: list[Inline]
    colspan: int = 1
    rowspan: int = 1
    header: bool = False  # <th>
    align: str | None = None  # left | center | right


@dataclass
class TableData:
    """An XML ``<table>``. Rows are lists of cells; spans are resolved by the renderer."""

    head: list[list[TableCell]]
    body: list[list[TableCell]]
    foot: list[list[TableCell]]
    ncols: int
    malformed: bool = False  # ragged rows / spans running off the grid


@dataclass
class TableBlock:
    id: str | None
    label: str | None
    title: list[Inline]
    caption: list[Block]
    graphics: list[str]  # image slices (every table seen on bioRxiv so far is images only)
    has_xml_table: bool = False
    footer: list[Block] = field(default_factory=list)
    table: TableData | None = None


@dataclass
class Supplement:
    id: str | None
    label: str | None
    caption: list[Block]
    href: str | None


@dataclass
class Section:
    title: list[Inline]  # may be empty (untitled section)
    blocks: list[Block]
    id: str | None = None
    kind: str | None = None  # sec-type, or "ack", "glossary", "notes", ...


@dataclass
class ReferenceListMarker:
    """Where a reference list sits among the back-matter blocks. Papers can have several
    (e.g. main + supplementary references); each lists only its own entries."""

    title: list[Inline]
    ref_ids: list[str] = field(default_factory=list)  # empty: all references


Block = (
    Paragraph | ListBlock | DefList | Quote | Preformatted | DisplayMath | Figure | TableBlock
    | Supplement | Section | ReferenceListMarker
)


# ---------------------------------------------------------------------------------------
# Front matter + references
# ---------------------------------------------------------------------------------------
@dataclass
class Author:
    given: str | None
    surname: str | None
    collab: str | None = None  # consortium name instead of a person
    orcid: str | None = None
    aff_ids: list[str] = field(default_factory=list)
    corresponding: bool = False
    equal_contrib: bool = False
    note_marks: list[str] = field(default_factory=list)  # marks of Article.author_notes on this author

    @property
    def display_name(self) -> str:
        if self.collab:
            return self.collab
        return " ".join(p for p in (self.given, self.surname) if p)


@dataclass
class Affiliation:
    id: str
    content: list[Inline]  # without the source label


@dataclass
class Reference:
    id: str
    number: int  # 1-based position within its own reference list (as the source numbers it)
    label: str | None  # as printed in the source ("1.", "1")
    content: list[Inline]  # mixed citation: the provided text; element citation: formatted
    kind: Literal["mixed", "element"]
    citation_type: str | None = None
    doi: str | None = None
    pmid: str | None = None
    url: str | None = None


@dataclass
class Article:
    doi: str | None
    title: list[Inline]
    authors: list[Author]
    affiliations: list[Affiliation]  # ordered by first appearance in the author list
    corresp: list[list[Inline]]  # corresponding-author notes (emails etc.), labels stripped
    equal_contrib_note: list[Inline] | None
    abstract: list[Block]
    keywords: list[str]
    license_text: list[Inline]
    license_url: str | None
    body: list[Block]
    back: list[Block]
    references: list[Reference]
    cite_mode: Literal["numeric", "author-year"]
    # From the bioRxiv API / fetch manifest, when available.
    server: str | None = None
    version: int | None = None
    posted: str | None = None
    category: str | None = None
    license_code: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Repairs applied to the source markup (e.g. superscripts wrongly tagged as citations).
    corrections: list[str] = field(default_factory=list)
    # Set when the preprint was withdrawn: {"version", "date", "statement"} (from the API).
    withdrawn: dict | None = None
    # The source numbers its own sections ("2.2. Model" titles or <label>s): don't add more.
    source_numbered: bool = False
    # Other author notes (present addresses, "lead contact", ...): (mark, text), shown under the
    # corresponding-author lines; marks are assigned here (the source's are often numbers that
    # would clash with the renumbered affiliations).
    author_notes: list[tuple[str, list[Inline]]] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# JSON snapshot support
# ---------------------------------------------------------------------------------------
def to_data(obj: Any) -> Any:
    """Recursively convert the model to JSON-able data, tagging node types."""
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {"type": type(obj).__name__}
        for f in fields(obj):
            out[f.name] = to_data(getattr(obj, f.name))
        return out
    if isinstance(obj, list | tuple):
        return [to_data(x) for x in obj]
    return obj


def plain_text(inlines: list[Inline]) -> str:
    """Flatten inline content to text (for short titles, logs, tests)."""
    parts: list[str] = []
    for node in inlines:
        if isinstance(node, Text):
            parts.append(node.text)
        elif isinstance(node, Styled | Link | XRef):
            parts.append(plain_text(node.children))
        elif isinstance(node, InlineMath):
            parts.append(node.text)
        elif isinstance(node, LineBreak):
            parts.append(" ")
        elif isinstance(node, Cite):
            parts.append("[" + ",".join(node.ref_ids) + "]")
    return "".join(parts)
