"""Stage 3 - Parse: JATS XML -> ``Article``.

Shaped by the bioRxiv/medRxiv JATS actually observed (5 fixtures, Oct 2026):

* References are ``<ref><label>1.</label><citation ...>`` - neither ``element-citation``
  nor ``mixed-citation``. ``<citation>`` is mixed content (punctuation between tagged
  parts, ``ref:use-reference-as-is="yes"``), so it is kept as provided text, the usual
  treatment of mixed citations. ``element-citation`` is still formatted if it appears.
* Citations are numeric groups wrapped in ``<sup>`` (``<sup><xref>3</xref>,<xref>4</xref></sup>``)
  or in literal brackets (``[<xref>1</xref>–<xref>4</xref>]``). A dash between two xrefs means
  a range (1–4 = 1,2,3,4); hyphens are used for this too. Wrappers/brackets are dropped
  so the template decides superscript vs bracketed style.
* Figures, display formulas etc. can sit inside ``<p>``; they are hoisted out as blocks.
* Section titles carry no numbers (``<label>`` absent), so the template numbers headings.
* bioRxiv's conversion links *any* superscript number to the reference with that number.
  Two cases are unambiguous and are unlinked again (``_unlink_false_citations``):
  decimal superscripts such as GPCR residue numbering ``D118<sup>3.49</sup>`` (split into
  "citations" 3 and 49), and powers of ten ``1.2×10<sup>6</sup>``. Ambiguous ones such as a
  peptide range ``NTS<sup>8–13</sup>`` cannot be told apart from real citations and are kept.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from lxml import etree

from .model import (
    Affiliation,
    Article,
    Author,
    Block,
    Cite,
    DefItem,
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
    Reference,
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

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
HWP_ID = "{http://schema.highwire.org/Journal}id"

_STYLE_TAGS = {
    "bold": "bold", "b": "bold",
    "italic": "italic", "i": "italic",
    "sup": "sup", "sub": "sub",
    "sc": "sc", "monospace": "mono", "underline": "underline",
    "strike": "strike", "overline": "overline",
}
# Elements whose children are rendered in place (no styling of their own).
_TRANSPARENT = {
    "roman", "sans-serif", "named-content", "styled-content", "span", "abbrev", "x",
    "institution", "institution-wrap", "country", "addr-line", "city", "state", "postal-code",
    "funding-source", "award-id", "kwd", "term", "def-head", "term-head", "hi",
    # citation parts (mixed content keeps their text)
    "string-name", "name", "surname", "given-names", "prefix", "suffix", "person-group", "collab",
    "article-title", "source", "year", "month", "day", "season", "volume", "issue", "fpage",
    "lpage", "elocation-id", "publisher-name", "publisher-loc", "chapter-title", "edition",
    "conf-name", "conf-date", "conf-loc", "comment", "date-in-citation", "size", "trans-title",
    "trans-source", "part-title", "role", "data-title", "version", "issue-title", "series",
    "page-range", "patent", "std", "gov", "access-date", "anonymous", "date", "label", "issn", "isbn",
}
_IGNORE = {
    "object-id", "alt-text", "long-desc", "sec-meta", "permissions", "attrib", "counts",
    "self-uri", "related-article", "related-object", "custom-meta-group", "processing-meta",
    "institution-id",  # ROR URLs inside funding sources / affiliations
}
_BLOCK_TAGS = {
    "p", "sec", "fig", "fig-group", "table-wrap", "table-wrap-group", "disp-formula",
    "disp-formula-group", "list", "def-list", "disp-quote", "boxed-text", "preformat", "code",
    "supplementary-material", "graphic", "media", "statement", "array", "chem-struct-wrap",
    "speech", "verse-group",
}
_RANGE_SEPS = {"–", "—", "-", "‒", "−", "to"}
_SEP_RE = re.compile(r"\s*(?:[,;–—\-‒−]|and|&|to)?\s*")
_CITE_JOIN_RE = re.compile(r"\s*[,;]?\s*")  # between two bracketed groups: "[5], [6]"
_NUMERIC_CITE_RE = re.compile(r"^\s*[\[(]?\s*\d+[a-z]?\s*[\])]?\s*$")
_WS_RE = re.compile(r"[ \t\r\n\f\v]+")


@dataclass
class _CiteAtom:
    rids: list[str]


@dataclass
class _Hoist:
    blocks: list[Block]


def _local(el: Any) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _clean_label(text: str | None) -> str | None:
    if not text:
        return None
    text = _WS_RE.sub(" ", text).strip().rstrip(":.").strip()
    return text or None


def parse_xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)
    return etree.fromstring(data, parser=parser)


# A character or entity reference escaped twice in the source: "Interferon &amp;#x3b3;" reads
# "Interferon &#x3b3;", "Proteomics &amp;amp; Bioinformatics" reads "Proteomics &amp; ..." (both
# seen in reference lists taken from Crossref metadata).
_DOUBLE_ESCAPED = re.compile(rb"&amp;(?:#x([0-9a-fA-F]{1,6})|#([0-9]{1,7})|(amp|lt|gt|quot|apos));")
_PREDEFINED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _undo_double_escapes(data: bytes) -> tuple[bytes, list[str]]:
    """Undo one level of escaping of such references. Returns (data, correction notes)."""
    fixed: list[tuple[str, str]] = []  # (reference, character)

    def fix(m: re.Match[bytes]) -> bytes:
        hexa, dec, name = (g.decode() if g else None for g in m.groups())
        ref = m.group(0)[len(b"&amp;") :]
        if name:
            fixed.append(("&" + ref.decode(), _PREDEFINED[name]))
            return b"&" + ref
        code = int(hexa, 16) if hexa else int(dec or "0")
        if not (code in (0x9, 0xA, 0xD) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            return m.group(0)  # not a character XML allows: leave the text as it is
        fixed.append(("&" + ref.decode(), chr(code)))
        return b"&" + ref

    data = _DOUBLE_ESCAPED.sub(fix, data)
    if not fixed:
        return data, []
    n, (ref, char) = len(fixed), fixed[0]
    return data, [f'Decoded {n} doubly escaped character reference{"s" if n > 1 else ""} in the source XML (e.g. "{ref}" -> "{char}")']


# Code-formatted text glued to the next word: "a <monospace>GT</monospace>dinucleotide",
# "in <monospace>G</monospace>and" (2025.06.16.660047: the authors' PDF has the spaces, the XML
# lost all 14). Only a capital or digit before a lowercase word: "<monospace>ID</monospace>s"
# stays a plural.
_GLUED_CODE = re.compile(rb"([A-Z0-9])</monospace>(?=[a-z]{2})")


def _space_glued_code(data: bytes) -> tuple[bytes, list[str]]:
    examples: list[bytes] = []

    def fix(m: re.Match[bytes]) -> bytes:
        examples.append(m.string[max(0, m.start() - 12) : m.end() + 12])
        return m.group(1) + b"</monospace> "

    data = _GLUED_CODE.sub(fix, data)
    if not examples:
        return data, []
    n = len(examples)
    m = re.search(rb">([^<>]*)</monospace>([a-z]+)", examples[0])
    eg = f' (e.g. "{(m.group(1) + m.group(2)).decode(errors="replace")}")' if m else ""
    return data, [f"Inserted {n} missing space{'s' if n > 1 else ''} after code-formatted text{eg}"]


def parse_jats(data: bytes, *, meta: Any = None) -> Article:
    """Parse JATS bytes. ``meta`` may be a fetch ``Manifest`` (adds server/version/date)."""
    data, fixed = _undo_double_escapes(data)
    data, spaced = _space_glued_code(data)
    fixed += spaced
    parser = _Parser(parse_xml(data))
    parser.corrections[:0] = fixed
    return parser.article(meta)


class _Parser:
    def __init__(self, root: etree._Element):
        self.root = root
        self.warnings: list[str] = []
        self._warned: set[str] = set()
        self.corrections = _unlink_false_citations(root)
        self.ref_elems = [r for r in root.iterfind(".//back//ref-list//ref")] or list(root.iter("ref"))
        self.ref_pos = {r.get("id"): i for i, r in enumerate(self.ref_elems) if r.get("id")}
        self.cite_mode = self._detect_cite_mode()

    # -- warnings ------------------------------------------------------------------------
    def warn(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self.warnings.append(message)
        log.warning("%s", message)

    def _unknown(self, el: Any, context: str) -> None:
        tag = _local(el)
        self.warn(f"unknown:{tag}", f"Unknown JATS element <{tag}> in {context}: rendered as plain text")

    # -- citation style ------------------------------------------------------------------
    def _detect_cite_mode(self) -> str:
        texts = ["".join(x.itertext()) for x in self.root.iter("xref") if x.get("ref-type") == "bibr"]
        if not texts:
            return "numeric"
        numeric = sum(1 for t in texts if not t.strip() or _NUMERIC_CITE_RE.match(t))
        return "numeric" if numeric >= 0.9 * len(texts) else "author-year"

    # -- inline content ------------------------------------------------------------------
    def inlines(self, el: Any, *, skip: frozenset[str] = frozenset({"label"}), hoist: bool = False) -> list[Any]:
        nodes: list[Any] = []
        self._add_text(nodes, el.text)
        for child in el:
            if isinstance(child.tag, str):
                tag = _local(child)
                if tag in skip or tag in _IGNORE:
                    pass
                else:
                    nodes.extend(self._inline(child, hoist=hoist))
            self._add_text(nodes, child.tail)
        return self._post(nodes)

    def _add_text(self, nodes: list[Any], text: str | None) -> None:
        if text:
            text = _WS_RE.sub(" ", text)
            if nodes and isinstance(nodes[-1], Text):
                nodes[-1] = Text(nodes[-1].text + text)
            else:
                nodes.append(Text(text))

    def _inline(self, el: Any, *, hoist: bool) -> list[Any]:
        tag = _local(el)
        if tag in _STYLE_TAGS:
            kids = self.inlines(el, hoist=hoist)
            cites = [k for k in kids if isinstance(k, Cite)]
            if len(cites) == 1 and all(isinstance(k, Cite) or (isinstance(k, Text) and not k.text.strip()) for k in kids):
                return cites  # <sup><xref>…</xref></sup>: the template styles citations
            return [Styled(_STYLE_TAGS[tag], kids)] if kids else []  # type: ignore[arg-type]
        if tag == "xref":
            return self._xref(el)
        if tag in ("ext-link", "uri"):
            kids = self.inlines(el)
            if not kids:
                return []  # empty machine links (Europe PMC adds doi/pmid/pmcid/google-scholar ones)
            url = _link_url(el.get(XLINK_HREF) or "".join(el.itertext()).strip(), el.get("ext-link-type"))
            return [Link(url, kids)] if url else kids
        if tag == "email":
            addr = "".join(el.itertext()).strip()
            return [Link(f"mailto:{addr}", [Text(addr)])]
        if tag == "pub-id":
            value = "".join(el.itertext()).strip()
            if el.get("pub-id-type") == "doi" and value:
                return [Link(f"https://doi.org/{value}", [Text(value)])]
            return [Text(value)] if value else []
        if tag == "etal":
            return self.inlines(el) or [Text("et al.")]
        if tag == "inline-formula":
            return [self._inline_math(el)]
        if tag == "math":
            return [InlineMath(text=_WS_RE.sub(" ", "".join(el.itertext())).strip(), mathml=etree.tostring(el, encoding="unicode"))]
        if tag == "tex-math":
            return [InlineMath(text=(el.text or "").strip(), tex=(el.text or "").strip())]
        if tag == "inline-graphic":
            href = el.get(XLINK_HREF)
            return [InlineGraphic(href)] if href else []
        if tag == "break":
            return [LineBreak()]
        if tag in _TRANSPARENT:
            return self.inlines(el, skip=frozenset(), hoist=hoist) if tag != "label" else []
        if tag in ("fn", "target", "alternatives"):
            return self.inlines(el, hoist=hoist)
        if tag in _BLOCK_TAGS:
            blocks = self.block(el)
            if hoist:
                return [_Hoist(blocks)]
            self.warn(f"block-in-inline:{tag}", f"<{tag}> inside inline text was flattened to plain text")
            return [Text(" " + _WS_RE.sub(" ", "".join(el.itertext())).strip() + " ")]
        self._unknown(el, "text")
        return self.inlines(el, hoist=hoist)

    def _xref(self, el: Any) -> list[Any]:
        kind = el.get("ref-type") or "other"
        rids = (el.get("rid") or "").split()
        if kind == "bibr" and self.cite_mode == "numeric" and rids:
            return [_CiteAtom(rids)]
        kids = self.inlines(el)
        if not rids:
            return kids
        return [XRef(kind, rids[0], kids)]

    def _post(self, nodes: list[Any]) -> list[Any]:
        """Group citation atoms into Cite nodes; drop brackets/space around numeric citations."""
        out: list[Any] = []
        i = 0
        while i < len(nodes):
            node = nodes[i]
            if not isinstance(node, _CiteAtom):
                out.append(node)
                i += 1
                continue
            group = [node]
            seps: list[str] = []
            j = i + 1
            while j < len(nodes):
                if isinstance(nodes[j], _CiteAtom):
                    seps.append("")
                    group.append(nodes[j])
                    j += 1
                elif (
                    isinstance(nodes[j], Text)
                    and j + 1 < len(nodes)
                    and isinstance(nodes[j + 1], _CiteAtom)
                    and _SEP_RE.fullmatch(nodes[j].text)
                ):
                    seps.append(nodes[j].text.strip())
                    group.append(nodes[j + 1])
                    j += 2
                else:
                    break
            out.append(Cite(self._expand(group, seps)))
            i = j

        for k, node in enumerate(out):
            if not isinstance(node, Cite):
                continue
            prev = out[k - 1] if k > 0 and isinstance(out[k - 1], Text) else None
            nxt = out[k + 1] if k + 1 < len(out) and isinstance(out[k + 1], Text) else None
            if prev is not None and nxt is not None:
                m1 = re.search(r"\s*([\[(])\s*$", prev.text)
                m2 = re.match(r"\s*([\])])", nxt.text)
                if m1 and m2 and {m1.group(1), m2.group(1)} in ({"[", "]"}, {"(", ")"}):
                    prev.text = prev.text[: m1.start()]
                    nxt.text = nxt.text[m2.end() :]
            if prev is not None:
                prev.text = prev.text.rstrip()
        out = [n for n in out if not (isinstance(n, Text) and n.text == "")]
        # "[5], [6]": with the brackets gone, groups separated only by a comma or semicolon
        # would print as two superscripts with a baseline comma between; they are one citation.
        merged: list[Any] = []
        for node in out:
            if isinstance(node, Cite) and len(merged) >= 2 and isinstance(merged[-2], Cite) \
                    and isinstance(merged[-1], Text) and _CITE_JOIN_RE.fullmatch(merged[-1].text):
                merged.pop()
                node = Cite(list(dict.fromkeys(merged.pop().ref_ids + node.ref_ids)))
            elif isinstance(node, Cite) and merged and isinstance(merged[-1], Cite):
                node = Cite(list(dict.fromkeys(merged.pop().ref_ids + node.ref_ids)))
            merged.append(node)
        return merged

    def _expand(self, group: list[_CiteAtom], seps: list[str]) -> list[str]:
        ids: list[str] = list(group[0].rids)
        for atom, sep in zip(group[1:], seps, strict=True):
            if sep in _RANGE_SEPS and ids and atom.rids:
                a, b = self.ref_pos.get(ids[-1]), self.ref_pos.get(atom.rids[0])
                if a is not None and b is not None and b > a + 1:
                    ids.extend(self.ref_elems[p].get("id") for p in range(a + 1, b))
            ids.extend(atom.rids)
        for rid in ids:
            if rid not in self.ref_pos:
                self.warn(f"cite:{rid}", f"Citation points to unknown reference id {rid!r}")
        seen: set[str] = set()
        return [r for r in ids if not (r in seen or seen.add(r))]

    def _inline_math(self, el: Any) -> InlineMath:
        tex = el.find(".//tex-math")
        mml = next((m for m in el.iter() if _local(m) == "math"), None)
        graphic = el.find(".//inline-graphic")
        if graphic is None:
            graphic = el.find(".//graphic")
        return InlineMath(
            text=_WS_RE.sub(" ", "".join(mml.itertext() if mml is not None else el.itertext())).strip(),
            tex=tex.text.strip() if tex is not None and tex.text else None,
            mathml=etree.tostring(mml, encoding="unicode") if mml is not None else None,
            image=graphic.get(XLINK_HREF) if graphic is not None else None,
        )

    def text_of(self, el: Any | None, *, skip: frozenset[str] = frozenset({"label"})) -> list[Inline]:
        """Inline content with hoisted blocks dropped (titles, labels, captions' titles)."""
        if el is None:
            return []
        return _trim([n for n in self.inlines(el, skip=skip) if not isinstance(n, _Hoist)])

    # -- blocks --------------------------------------------------------------------------
    def blocks(self, el: Any, *, skip: frozenset[str] = frozenset({"title", "label"})) -> list[Block]:
        out: list[Block] = []
        if el.text and el.text.strip():  # stray text directly in a container
            out.append(Paragraph(_trim(self.inlines(_text_only(el)))))
        for child in el:
            if isinstance(child.tag, str) and _local(child) not in skip and _local(child) not in _IGNORE:
                out.extend(self.block(child))
            if child.tail and child.tail.strip():
                out.append(Paragraph([Text(_WS_RE.sub(" ", child.tail).strip())]))
        return out

    def block(self, el: Any) -> list[Block]:
        tag = _local(el)
        if tag == "p":
            return self.paragraph(el)
        if tag in ("sec", "ack", "glossary", "notes", "app", "boxed-text", "statement", "bio"):
            return [self.section(el)]
        if tag in ("fig-group", "table-wrap-group", "disp-formula-group", "app-group", "fn-group", "speech", "verse-group"):
            if tag == "fn-group":
                return [Section(self.text_of(el.find("title")) or [Text("Notes")], self.blocks(el), el.get("id"), "notes")]
            return self.blocks(el)
        if tag == "fn":
            return self.blocks(el)
        if tag == "list":
            return [self.list_block(el)]
        if tag == "def-list":
            items = [
                DefItem(self.text_of(item.find("term")), self.blocks(item.find("def")) if item.find("def") is not None else [])
                for item in el.iter("def-item")
            ]
            return [DefList(items)]
        if tag == "fig":
            return [self.figure(el)]
        if tag == "table-wrap":
            return [self.table(el)]
        if tag == "disp-formula":
            return [self.display_math(el)]
        if tag in ("supplementary-material", "media"):
            return [self.supplement(el)]
        if tag == "graphic":
            href = el.get(XLINK_HREF)
            return [Figure(el.get("id"), None, [], [], [href])] if href else []
        if tag == "disp-quote":
            return [Quote(self.blocks(el))]
        if tag in ("preformat", "code"):
            return [Preformatted("".join(el.itertext()))]
        if tag == "ref-list":
            ids = [r.get("id") or "" for r in el.iter("ref")]
            title = self.text_of(el.find("title"))
            if not title and _local(el.getparent()) not in ("sec", "ref-list"):
                title = [Text("References")]  # a titled enclosing <sec> already supplies the heading
            return [ReferenceListMarker(title, ids)]
        if tag in ("title", "label", "caption"):
            return []
        self._unknown(el, "body")
        text = _WS_RE.sub(" ", "".join(el.itertext())).strip()
        return [Paragraph([Text(text)])] if text else []

    def paragraph(self, el: Any) -> list[Block]:
        out: list[Block] = []
        current: list[Any] = []
        continues = False
        for node in self.inlines(el, hoist=True):
            if isinstance(node, _Hoist):
                if _has_content(current):
                    out.append(Paragraph(_trim(current), continues))
                out.extend(node.blocks)
                current, continues = [], True
            else:
                current.append(node)
        if _has_content(current):
            out.append(Paragraph(_trim(current), continues))
        return out

    def section(self, el: Any) -> Section:
        tag = _local(el)
        title = self.text_of(el.find("title"))
        label = " ".join((el.findtext("label") or "").split())  # keep "a." / "2.2." as printed
        if label and title:  # e.g. <label>2.2</label><title>Model</title>
            title = [Text(label + " "), *title]
        if not title and tag == "ack":
            title = [Text("Acknowledgements")]
        if not title and tag == "glossary":
            title = [Text("Abbreviations")]
        if not title and tag == "boxed-text":
            title = self.text_of(el.find("caption/title"))
        kind = el.get("sec-type") or el.get("notes-type") or (tag if tag != "sec" else None)
        return Section(title, self.blocks(el, skip=frozenset({"title", "label", "caption"} if tag == "boxed-text" else {"title", "label"})), el.get("id"), kind)

    def list_block(self, el: Any) -> ListBlock:
        list_type = el.get("list-type") or ""
        ordered = list_type.startswith(("order", "alpha", "roman")) or list_type == "number"
        items = [self.blocks(item) for item in el if _local(item) == "list-item"]
        return ListBlock(ordered, items, list_type if list_type.startswith(("alpha", "roman")) else None)

    def _caption(self, el: Any) -> tuple[list[Inline], list[Block]]:
        cap = el.find("caption")
        if cap is None:
            return [], []
        title = self.text_of(cap.find("title"))
        # Nature-style "Fig. 1 | Title": the separator is inside <title> (seen in bioRxiv XML).
        if title and isinstance(title[0], Text):
            title[0] = Text(re.sub(r"^\s*[|:.\u2013\u2014-]\s*", "", title[0].text))
            title = _trim(title)
        blocks = self.blocks(cap)
        # No <title>, and the first paragraph opens with the separator instead: "– Title ..."
        if not title and blocks and isinstance(blocks[0], Paragraph) and blocks[0].content \
                and isinstance(blocks[0].content[0], Text):
            first = blocks[0].content[0]
            stripped = re.sub(r"^\s*[|:\u2013\u2014-]\s+", "", first.text)
            if stripped != first.text:
                blocks[0].content[0] = Text(stripped)
        return title, blocks

    def figure(self, el: Any) -> Figure:
        label = _clean_label(plain_text(self.text_of(el.find("label"), skip=frozenset())))
        title, caption = self._caption(el)
        graphics = [g.get(XLINK_HREF) for g in el.iter("graphic") if g.get(XLINK_HREF)]
        fig_id = el.get("id") or ""
        supplementary = (
            el.get("fig-type") in ("supplementary", "supplementary-figure", "extended-data")
            or bool(label and re.match(r"(?i)(supplementary|extended data|supporting|fig(ure)?\.?\s*S\d)", label))
            or bool(re.match(r"(?i)figs\d", fig_id))
        )
        return Figure(el.get("id"), label, title, caption, graphics, supplementary)

    def table(self, el: Any) -> TableBlock:
        label = _clean_label(plain_text(self.text_of(el.find("label"), skip=frozenset())))
        title, caption = self._caption(el)
        graphics = [g.get(XLINK_HREF) for g in el.iter("graphic") if g.get(XLINK_HREF)]
        foot = el.find("table-wrap-foot")
        tab = el.find(".//table")
        return TableBlock(
            el.get("id"), label, title, caption, graphics,
            has_xml_table=tab is not None,
            footer=self.blocks(foot) if foot is not None else [],
            table=self.xml_table(tab) if tab is not None else None,
        )

    def xml_table(self, tab: Any) -> TableData:
        """<table> with thead/tbody/tfoot (or bare rows), th/td, colspan/rowspan."""
        head: list[list[TableCell]] = []
        body: list[list[TableCell]] = []
        foot: list[list[TableCell]] = []
        for tr in tab.iter("tr"):
            section = next((_local(a) for a in tr.iterancestors() if _local(a) in ("thead", "tbody", "tfoot", "table")), "table")
            row = [self._cell(c) for c in tr if _local(c) in ("td", "th")]
            {"thead": head, "tfoot": foot}.get(section, body).append(row)
        if not head and body and body[0] and all(c.header for c in body[0]):
            head.append(body.pop(0))  # no <thead>, but a leading row of <th>
        for row in head:
            for c in row:
                c.header = True
        for section in (head, body, foot):  # a rowspan past the end of its section is clipped
            for i, row in enumerate(section):  # there, as browsers do (seen in a PLOS table)
                for c in row:
                    c.rowspan = min(c.rowspan, len(section) - i)
        ncols, malformed = _grid_width(head + body + foot)
        if malformed:
            self.warn(f"table:{tab.getparent().get('id')}", "An XML table has ragged rows or spans running off the grid")
        return TableData(head, body, foot, ncols, malformed)

    def _cell(self, c: Any) -> TableCell:
        nodes: list[Any] = []
        for n in self.inlines(c, hoist=True):
            if isinstance(n, _Hoist):  # <p>/<list> inside a cell: flatten, one line per block
                for b in n.blocks:
                    if nodes:
                        nodes.append(LineBreak())
                    nodes.extend(b.content if isinstance(b, Paragraph) else [Text(_block_text(b))])
            else:
                nodes.append(n)

        def span(attr: str) -> int:
            try:
                return max(1, int(c.get(attr, "1")))
            except ValueError:
                return 1

        return TableCell(_trim(nodes), span("colspan"), span("rowspan"), _local(c) == "th", c.get("align"))

    def display_math(self, el: Any) -> DisplayMath:
        m = self._inline_math(el)
        label = _clean_label(el.findtext("label"))
        return DisplayMath(el.get("id"), label, m.text, m.tex, m.mathml, m.image)

    def supplement(self, el: Any) -> Supplement:
        media = el if _local(el) == "media" else el.find(".//media")
        label = _clean_label(plain_text(self.text_of(el.find("label"), skip=frozenset())))
        _, caption = self._caption(el)
        return Supplement(el.get("id") or el.get(HWP_ID), label, caption, media.get(XLINK_HREF) if media is not None else None)

    # -- references ----------------------------------------------------------------------
    def references(self) -> list[Reference]:
        refs: list[Reference] = []
        within: dict[Any, int] = {}  # numbering restarts in each <ref-list>
        for ref in self.ref_elems:
            owner = next((a for a in ref.iterancestors() if _local(a) == "ref-list"), None)
            within[owner] = number = within.get(owner, 0) + 1
            holder = ref.find("citation-alternatives")
            holder = holder if holder is not None else ref
            kinds = ("mixed-citation", "element-citation", "citation", "nlm-citation")
            cit = next((c for kind in kinds for c in holder if _local(c) == kind), None)  # prefer the author's text
            label = _WS_RE.sub(" ", ref.findtext("label") or "").strip() or None
            target = cit if cit is not None else ref
            is_element = cit is not None and _local(cit) == "element-citation" and not _has_mixed_text(cit)
            content = self._format_element_citation(cit) if is_element else _trim(self.text_of(target))
            # Several works under one number ("[2] a) ...; b) ..."), each its own <citation> with
            # a <label>: all of them, not just the first (<citation-alternatives> are one work).
            siblings = [c for c in holder if cit is not None and _local(c) == _local(cit)] if holder is ref else []
            if len(siblings) > 1:
                content = []
                for c in siblings:
                    sub = c.findtext("label")
                    part = self._format_element_citation(c) if is_element else _trim(self.text_of(c))
                    content += [Text(" ")] if content else []
                    content += [Text(_WS_RE.sub(" ", sub).strip() + " ")] if sub and sub.strip() else []
                    content += part
            doi_el = target.find(".//pub-id[@pub-id-type='doi']")
            if doi_el is None:
                doi_el = target.find(".//ext-link[@ext-link-type='doi']")
                if doi_el is not None:
                    doi_el = _Text(doi_el.get(XLINK_HREF))
            pmid_el = target.find(".//pub-id[@pub-id-type='pmid']")
            link = next((e for e in target.iter() if _local(e) in ("ext-link", "uri") and e.get(XLINK_HREF)), None)
            refs.append(
                Reference(
                    id=ref.get("id") or f"ref-{number}",
                    number=number,
                    label=label,
                    content=content,
                    kind="element" if is_element else "mixed",
                    citation_type=(cit.get("citation-type") or cit.get("publication-type")) if cit is not None else None,
                    doi=doi_el.text.strip() if doi_el is not None and doi_el.text else None,
                    pmid=pmid_el.text.strip() if pmid_el is not None and pmid_el.text else None,
                    url=link.get(XLINK_HREF) if link is not None else None,
                )
            )
        return refs

    def _format_element_citation(self, cit: Any) -> list[Inline]:
        """Vancouver-like: Authors. Title. Source. Year;Vol(Issue):fpage–lpage."""
        names = []
        for n in cit.iter():
            t = _local(n)
            if t in ("name", "string-name"):
                sur, giv = n.findtext("surname") or "", n.findtext("given-names") or ""
                initials = "".join(p[0] for p in re.split(r"[\s.\-]+", giv) if p)
                names.append(f"{sur} {initials}".strip() if sur else _WS_RE.sub(" ", "".join(n.itertext())).strip())
            elif t == "collab":
                names.append(_WS_RE.sub(" ", "".join(n.itertext())).strip())
        if cit.find(".//etal") is not None:
            names.append("et al")
        out: list[Inline] = []
        if names:
            out.append(Text(", ".join(names) + ". "))
        title = cit.find("article-title") if cit.find("article-title") is not None else cit.find("chapter-title")
        if title is not None:
            out.extend(self.text_of(title))
            out.append(Text(". "))
        source = cit.find("source")
        if source is not None:
            out.append(Styled("italic", self.text_of(source)))
            out.append(Text(". "))
        year, vol, issue = cit.findtext("year"), cit.findtext("volume"), cit.findtext("issue")
        fpage, lpage, eloc = cit.findtext("fpage"), cit.findtext("lpage"), cit.findtext("elocation-id")
        tail = year or ""
        if vol:
            tail += f";{vol}" if tail else vol
        if issue:
            tail += f"({issue})"
        pages = f"{fpage}–{lpage}" if fpage and lpage else fpage or eloc
        if pages:
            tail += f":{pages}"
        if tail:
            out.append(Text(tail + ". "))
        doi = cit.findtext(".//pub-id[@pub-id-type='doi']")
        if doi:
            out.extend([Text("doi:"), Link(f"https://doi.org/{doi.strip()}", [Text(doi.strip())])])
        return _trim(out)

    # -- front matter --------------------------------------------------------------------
    def article(self, meta: Any = None) -> Article:
        am = self.root.find("front/article-meta")
        if am is None:
            raise ValueError("Not a JATS article: <front>/<article-meta> missing")

        author_contribs = [c for c in am.iter("contrib") if c.get("contrib-type", "author") == "author"]
        authors = [self._author(c) for c in author_contribs]

        aff_elems = {a.get("id"): a for a in am.iter("aff") if a.get("id")}
        order: list[str] = []
        for a in authors:
            for rid in a.aff_ids:
                if rid in aff_elems and rid not in order:
                    order.append(rid)
        order += [rid for rid in aff_elems if rid not in order]
        affiliations = [Affiliation(rid, _strip_punct(self.text_of(aff_elems[rid]))) for rid in order]

        notes = am.find("author-notes")
        corresp = [_strip_punct(self.text_of(c)) for c in notes.iter("corresp")] if notes is not None else []
        equal_note = None
        author_notes: list[tuple[str, list[Inline]]] = []
        if notes is not None:
            cited = [[r for x in c.iterfind("xref[@ref-type='fn']") for r in (x.get("rid") or "").split()] for c in author_contribs]
            equal_fns = notes.findall("fn[@fn-type='equal']")
            if equal_fns:
                fn = equal_fns[0]
                equal_note = _strip_punct(self.text_of(fn.find("p") if fn.find("p") is not None else fn))
            # Two equal-contribution groups (co-first and co-senior authors; 3/38 sampled): the
            # equal-contrib attribute cannot tell them apart, the authors' links to the notes can.
            # The first group keeps the equal-contribution mark, the others are notes below.
            grouped = len(equal_fns) > 1 and any(r == equal_fns[0].get("id") for rids in cited for r in rids)
            if grouped:
                for a, rids in zip(authors, cited, strict=True):
                    a.equal_contrib = equal_fns[0].get("id") in rids
            # Other notes: present addresses, "lead contact", deceased, ... Competing-interest
            # and funding notes go to the back matter (`_add_front_only_back_matter`).
            # A labelled note that no author points to has lost its link (seen: "** lead contact"
            # with no "**" in the author list) and is dropped; an unlabelled one is general.
            marks: dict[str, str] = {}
            for fn in notes.iterfind("fn"):
                if fn.get("fn-type") in _BACK_MATTER_FN or (fn.get("fn-type") == "equal" and (not grouped or fn is equal_fns[0])):
                    continue
                text = _strip_punct(self.text_of(fn.find("p") if fn.find("p") is not None else fn))
                linked = fn.get("id") is not None and any(fn.get("id") in rids for rids in cited)
                if not plain_text(text).strip() or (not linked and fn.find("label") is not None):
                    continue
                n = len(marks)
                mark = _NOTE_MARKS[n % len(_NOTE_MARKS)] * (1 + n // len(_NOTE_MARKS)) if linked else ""
                author_notes.append((mark, text))
                if linked:
                    marks[fn.get("id")] = mark
            for a, rids in zip(authors, cited, strict=True):
                a.note_marks = [marks[r] for r in rids if r in marks]

        abstracts = am.findall("abstract")
        main_abs = next((a for a in abstracts if not a.get("abstract-type")), abstracts[0] if abstracts else None)
        abstract = self.blocks(main_abs) if main_abs is not None else []

        lic = am.find("permissions/license")
        license_text: list[Inline] = []
        if lic is not None:
            lp = lic.find("license-p") if lic.find("license-p") is not None else lic.find("p")
            license_text = self.text_of(lp if lp is not None else lic)

        back_el = self.root.find("back")
        body_el = self.root.find("body")
        article = Article(
            doi=(am.findtext("article-id[@pub-id-type='doi']") or "").strip() or None,
            title=self.text_of(am.find("title-group/article-title")),
            authors=authors,
            affiliations=affiliations,
            corresp=[c for c in corresp if c],
            equal_contrib_note=equal_note,
            author_notes=author_notes,
            abstract=abstract,
            keywords=[_WS_RE.sub(" ", "".join(k.itertext())).strip() for k in am.iterfind("kwd-group/kwd")],
            license_text=license_text,
            license_url=lic.get(XLINK_HREF) if lic is not None else None,
            body=self.blocks(body_el) if body_el is not None else [],
            back=self.blocks(back_el) if back_el is not None else [],
            references=self.references(),
            cite_mode=self.cite_mode,  # type: ignore[arg-type]
        )
        if meta is not None:
            article.server = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}.get(getattr(meta, "server", ""), getattr(meta, "server", None))
            article.version = getattr(meta, "version", None)
            article.posted = getattr(meta, "date", None)
            article.category = getattr(meta, "category", None)
            article.license_code = getattr(meta, "license", None)
            article.withdrawn = getattr(meta, "withdrawn", None)
        def has_marker(blocks: list[Block]) -> bool:
            return any(isinstance(b, ReferenceListMarker) or (isinstance(b, Section) and has_marker(b.blocks)) for b in blocks)

        if article.references and not has_marker(article.body + article.back):  # Europe PMC puts it in <body>
            article.back.append(ReferenceListMarker([Text("References")]))
        self._add_front_only_back_matter(am, article)
        article.warnings = self.warnings
        article.corrections = self.corrections
        body_secs = list(body_el.iter("sec")) if body_el is not None else []
        numbered = sum(1 for s in body_secs if re.match(r"^\s*\d+(\.\d+)*\.?\s", s.findtext("title") or ""))
        article.source_numbered = bool(body_secs) and (
            any(s.find("label") is not None for s in body_secs) or numbered >= len(body_secs) / 2
        )
        return article

    # Back-matter statements that bioRxiv/medRxiv keep only in <front>: the submission
    # form's competing-interest statement (30/31 sampled papers), structured funding
    # (24/31) and medRxiv's "Author Declarations". Added unless the authors' own back
    # matter already has a section on the topic.
    _TOPICS: ClassVar[dict[str, re.Pattern[str]]] = {
        "funding": re.compile(r"fund|financial support|grant", re.IGNORECASE),
        "competing-interests": re.compile(r"compet|conflict|declaration of interest|disclosure|financial interest", re.IGNORECASE),
    }

    def _add_front_only_back_matter(self, am: Any, article: Article) -> None:
        def sections(blocks: list[Block]) -> list[Section]:
            out = []
            for b in blocks:
                if isinstance(b, Section):
                    out += [b, *sections(b.blocks)]
            return out

        back_secs = sections(article.back)  # nested too: "Declarations" > "Competing interests"
        present = " | ".join(plain_text(b.title) + " " + (b.kind or "") for b in back_secs)
        extra: list[Section] = []

        funding = am.find("funding-group")
        if funding is not None and not self._TOPICS["funding"].search(present):
            statement = funding.find("funding-statement")
            if statement is not None:
                blocks: list[Block] = [Paragraph(self.text_of(statement))]
            else:
                awards = []
                for g in funding.iter("award-group"):
                    sources = "; ".join(_WS_RE.sub(" ", plain_text(self.text_of(fs))).strip() for fs in g.iter("funding-source"))
                    ids = ", ".join(_WS_RE.sub(" ", a.text or "").strip() for a in g.iter("award-id") if a.text)
                    if sources:
                        awards.append(f"{sources} ({ids})" if ids else sources)
                blocks = [Paragraph([Text("; ".join(awards) + ".")])] if awards else []
            if blocks:
                extra.append(Section([Text("Funding")], blocks, None, "funding"))

        elsewhere_cache: list[str] = []

        def elsewhere() -> str:
            """Body + back text and link targets, whitespace-normalised (built on first use)."""
            if not elsewhere_cache:
                parts = [p for p in (self.root.find("body"), self.root.find("back")) if p is not None]
                text = " ".join(t for p in parts for t in [*p.itertext(), *(e.get(XLINK_HREF) or "" for e in p.iter("ext-link"))])
                elsewhere_cache.append(_WS_RE.sub(" ", text))
            return elsewhere_cache[0]

        titles = {"funding": "Funding", "competing-interests": "Competing interests"}

        def add_statement(topic: str, blocks: list[Block]) -> None:
            """A statement kept only in the front matter, unless the authors have a section on
            the topic or give the same text elsewhere (usually their acknowledgements)."""
            blocks = _drop_lead_label(blocks, self._TOPICS[topic])
            said = _WS_RE.sub(" ", " ".join(plain_text(b.content) for b in blocks if isinstance(b, Paragraph))).strip()
            if (
                said
                and not self._TOPICS[topic].search(present)
                and not any(s.kind == topic for s in extra)
                and " ".join(said.split()[:8]) not in elsewhere()
            ):
                extra.append(Section([Text(titles[topic])], blocks, None, topic))

        # Funding / conflict-of-interest footnotes among the author notes ("Funding: This study
        # received funding from the ERC ..." as fn-type="supported-by"). Before the submission
        # form's statements below: the authors' own wording wins over "The authors have declared
        # no competing interest."
        author_notes = am.find("author-notes")
        for fn in author_notes.iterfind("fn") if author_notes is not None else []:
            topic = next((t for t, types in _BACK_MATTER_FN_TOPICS.items() if fn.get("fn-type") in types), None)
            if topic is not None:
                add_statement(topic, self.blocks(fn))

        for notes in self.root.find("front").iter("notes"):
            kind = notes.get("notes-type")
            if kind == "financial-disclosure":
                # medRxiv's submission-form "Funding Statement" (the authors repeat it 6/8 sampled).
                add_statement("funding", self.blocks(notes))
            elif kind == "competing-interest-statement":
                add_statement("competing-interests", self.blocks(notes))
            elif kind == "disclosures":
                extra.append(Section(self.text_of(notes.find("title")) or [Text("Author declarations")], self.blocks(notes), None, "disclosures"))

        # Submission-form fields bioRxiv keeps in the front matter: the authors' summary of
        # what changed in a revised version, and data/code links. Links already given in the
        # text (usually the data availability statement) are not repeated.
        for group in self.root.find("front").iter("fn-group"):
            kind = group.get("content-type")
            if kind == "summary-of-updates":
                blocks = [b for fn in group.iter("fn") for b in self.blocks(fn)]
                if blocks:
                    extra.append(Section([Text("Summary of updates")], blocks, None, "summary-of-updates"))
            elif kind == "external-links":
                urls = []
                for link in group.iter("ext-link"):
                    url = _link_url(link.get(XLINK_HREF) or "".join(link.itertext()).strip(), link.get("ext-link-type"))
                    tail = re.sub(r"^https?://(dx\.)?[^/]+/", "", url)  # DOI or path, as cited in the text
                    if url and url not in urls and tail not in elsewhere():
                        urls.append(url)
                if urls:
                    items = [[Paragraph([Link(u, [Text(u)])])] for u in urls]
                    extra.append(Section([Text("External links")], [ListBlock(False, items)], None, "external-links"))

        if extra:
            order = ("funding", "competing-interests", "disclosures", "summary-of-updates", "external-links")
            extra.sort(key=lambda sec: order.index(sec.kind) if sec.kind in order else len(order))
            at = next((i for i, b in enumerate(article.back) if isinstance(b, ReferenceListMarker)), len(article.back))
            article.back[at:at] = extra

    def _author(self, contrib: Any) -> Author:
        name = contrib.find("name")
        if name is None:
            name = contrib.find("string-name")
        collab = contrib.find("collab")
        orcid_el = contrib.find("contrib-id[@contrib-id-type='orcid']")
        orcid = None
        if orcid_el is not None and orcid_el.text:
            m = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", orcid_el.text)
            orcid = m.group(0) if m else None
        aff_ids = [rid for x in contrib.iterfind("xref[@ref-type='aff']") for rid in (x.get("rid") or "").split()]
        surname = given = None
        if name is not None:
            surname = _WS_RE.sub(" ", name.findtext("surname") or "").strip() or None
            given = _WS_RE.sub(" ", name.findtext("given-names") or "").strip() or None
            suffix = name.findtext("suffix")
            if surname and suffix:
                surname = f"{surname} {suffix.strip()}"
            if not surname and not given:
                given = _WS_RE.sub(" ", "".join(name.itertext())).strip() or None
        return Author(
            given=given,
            surname=surname,
            collab=_WS_RE.sub(" ", "".join(collab.itertext())).strip() if collab is not None and name is None else None,
            orcid=orcid,
            aff_ids=aff_ids,
            corresponding=contrib.get("corresp") == "yes" or contrib.find("xref[@ref-type='corresp']") is not None,
            equal_contrib=contrib.get("equal-contrib") == "yes",
        )


# Author-note footnote types that are back-matter statements: topic -> fn-types.
_BACK_MATTER_FN_TOPICS = {
    "funding": ("supported-by", "financial-disclosure"),
    "competing-interests": ("conflict", "coi-statement"),
}
_BACK_MATTER_FN = {t for types in _BACK_MATTER_FN_TOPICS.values() for t in types}
_NOTE_MARKS = ("‡", "§", "¶", "‖")  # † and * are the equal-contribution and corresponding marks


# ---------------------------------------------------------------------------------------
# Superscripts wrongly tagged as citations
# ---------------------------------------------------------------------------------------
_DECIMAL_SUP = re.compile(r"^\d+\.\d+$")  # 3.49 (GPCR Ballesteros-Weinstein numbering, versions)
_POWER_SUP = re.compile(r"^[−-]?\d+$")  # 6 in 1.2×10^6
_TIMES_TEN = re.compile(r"[×xX✕∗*·⋅]\s?10\s*$")  # "2∗10" seen in a random-sample paper
_PLURAL = {"decimal superscript": "decimal superscripts", "power of ten": "powers of ten"}


def _unlink_false_citations(root: Any) -> list[str]:
    """Turn bibr xrefs that are really parts of numbers back into plain superscript text."""
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    for run in _sup_runs(root):
        xrefs = [x for s in run for x in s.iter("xref") if x.get("ref-type") == "bibr"]
        if not xrefs:
            continue
        text = "".join("".join(s.itertext()) for s in run).strip()
        before = _text_before(run[0])
        if _DECIMAL_SUP.match(text):
            kind = "decimal superscript"
        elif _POWER_SUP.match(text) and _TIMES_TEN.search(before):
            kind = "power of ten"
        else:
            continue
        for x in xrefs:
            _unwrap(x)
        counts[kind] = counts.get(kind, 0) + 1
        examples.setdefault(kind, f"{before.split()[-1] if before.split() else ''}^{text}")
    out = [
        f"Unlinked {n} {kind if n == 1 else _PLURAL[kind]} that the source XML tagged as citations (e.g. {examples[kind]})"
        for kind, n in counts.items()
    ]
    for line in out:
        log.debug("%s", line)  # the CLI reports these as "fixed: ..."
    return out


def _sup_runs(root: Any) -> list[list[Any]]:
    """Maximal runs of sibling <sup> elements with nothing at all between them."""
    runs: list[list[Any]] = []
    seen: set[Any] = set()
    for sup in root.iter("sup"):
        if sup in seen:
            continue
        run = [sup]
        nxt = sup.getnext()
        while nxt is not None and _local(nxt) == "sup" and not run[-1].tail:
            run.append(nxt)
            nxt = nxt.getnext()
        seen.update(run)
        runs.append(run)
    return runs


def _text_before(el: Any, n: int = 30) -> str:
    prev = el.getprevious()
    if prev is not None:
        text = prev.tail or ""
        if len(text) < n:  # e.g. "×10" split across an <italic>x</italic>
            text = "".join(prev.itertext()) + text
    else:
        text = el.getparent().text or ""
    return text[-n:]


def _unwrap(el: Any) -> None:
    """Replace ``el`` by its text content, keeping its tail."""
    parent = el.getparent()
    text = "".join(el.itertext()) + (el.tail or "")
    prev = el.getprevious()
    if prev is not None:
        prev.tail = (prev.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
    parent.remove(el)


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def _grid_width(rows: list[list[TableCell]]) -> tuple[int, bool]:
    """Simulate span occupancy. Returns (column count, malformed?)."""
    occupied: dict[int, set[int]] = {}
    widths = []
    for r, row in enumerate(rows):
        taken = occupied.setdefault(r, set())
        col = 0
        for cell in row:
            while col in taken:
                col += 1
            for dr in range(cell.rowspan):
                occupied.setdefault(r + dr, set()).update(range(col, col + cell.colspan))
            col += cell.colspan
        widths.append(max(taken) + 1 if taken else 0)
    overflow = any(r >= len(rows) for r in occupied if occupied[r])
    ncols = max(widths, default=0)
    return ncols, overflow or len(set(widths)) > 1 or ncols == 0


def _block_text(b: Any) -> str:
    from .model import plain_text as _pt

    if isinstance(b, ListBlock):
        return "; ".join(_pt(x.content) for item in b.items for x in item if isinstance(x, Paragraph))
    return _pt(getattr(b, "content", [])) if hasattr(b, "content") else ""


@dataclass
class _Text:  # stands in for an element whose .text is a value taken from an attribute
    text: str | None


def _link_url(href: str, kind: str | None) -> str:
    """ext-link hrefs are sometimes bare identifiers; turn those into URLs."""
    if not href or re.match(r"^[a-z][a-z0-9+.-]*:", href, re.IGNORECASE):
        return href
    if kind == "doi" or href.startswith("10."):
        return f"https://doi.org/{href}"
    if kind == "pmid":
        return f"https://pubmed.ncbi.nlm.nih.gov/{href}/"
    if kind == "pmcid":
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{href}/"
    return f"https://{href}" if "." in href.split("/")[0] else href


def _text_only(el: Any) -> Any:
    """A shallow copy of ``el`` holding only its leading text (for stray container text)."""
    clone = etree.Element("x")
    clone.text = el.text
    return clone


def _has_content(nodes: list[Any]) -> bool:
    return any(not (isinstance(n, Text) and not n.text.strip()) for n in nodes)


def _has_mixed_text(el: Any) -> bool:
    if el.text and el.text.strip(" \n\t.,;:()"):
        return True
    return any(c.tail and c.tail.strip(" \n\t.,;:()–-") for c in el)


def _trim(nodes: list[Any]) -> list[Any]:
    nodes = [n for n in nodes if not (isinstance(n, Text) and n.text == "")]
    if nodes and isinstance(nodes[0], Text):
        nodes[0] = Text(nodes[0].text.lstrip())
    if nodes and isinstance(nodes[-1], Text):
        nodes[-1] = Text(nodes[-1].text.rstrip())
    return [n for n in nodes if not (isinstance(n, Text) and n.text == "")]


def _drop_lead_label(blocks: list[Block], topic: re.Pattern[str]) -> list[Block]:
    """Drop a run-in label naming the topic ("**Funding**: This study ...") from the first
    paragraph: the statement gets a section title instead."""
    if not blocks or not isinstance(blocks[0], Paragraph) or not blocks[0].content:
        return blocks
    first, *rest = blocks[0].content
    if isinstance(first, Text):  # plain "Conflict of Interest: The author declares ..."
        m = re.match(r"\s*([^:.]{3,40}):\s+", first.text)
        if not (m and topic.search(m.group(1))):
            return blocks
        rest = [Text(first.text[m.end():]), *rest]
    elif not (isinstance(first, Styled) and topic.search(plain_text(first.children)) and len(plain_text(first.children)) < 40):
        return blocks
    if rest and isinstance(rest[0], Text):
        rest[0] = Text(rest[0].text.lstrip(" :.\u2013\u2014-"))
    content = [n for n in rest if not (isinstance(n, Text) and not n.text)]
    return [Paragraph(content, blocks[0].continues), *blocks[1:]] if content else blocks[1:]


def _strip_punct(nodes: list[Inline]) -> list[Inline]:
    """Trim whitespace and dangling separators (', ') left where a <label> was removed."""
    nodes = _trim(nodes)
    if nodes and isinstance(nodes[0], Text):
        nodes[0] = Text(nodes[0].text.lstrip(" ,;:"))
    if nodes and isinstance(nodes[-1], Text):
        nodes[-1] = Text(nodes[-1].text.rstrip(" ,;"))
    return [n for n in nodes if not (isinstance(n, Text) and n.text == "")]
