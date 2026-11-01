"""MathML -> Typst math (no dependencies).

None of the 31 bioRxiv/medRxiv papers sampled carried MathML (every equation was a GIF),
so this covers the common presentation-MathML subset and gives up - returning None, so the
caller falls back to the equation image - on anything else. Output is conservative:
single letters/digits/known symbols are emitted bare, every other character is escaped,
multi-letter identifiers become quoted text, and every construct uses explicit calls
(``attach``, ``frac``, ``sqrt``...), so the result always parses.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

MATHML_NS = "http://www.w3.org/1998/Math/MathML"

_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
    "log", "ln", "lg", "exp", "lim", "liminf", "limsup", "max", "min", "sup", "inf", "det", "dim",
    "gcd", "mod", "arg", "deg", "ker", "Pr",
}
_ACCENTS = {"¯": "overline", "‾": "overline", "^": "hat", "ˆ": "hat", "~": "tilde", "˜": "tilde", "→": "arrow", "⃗": "arrow", "˙": "dot", "¨": "dot.double", "ˇ": "caron", "˘": "breve"}
_UNDERS = {"_": "underline", "̲": "underline", "‾": "underline", "⏟": "underbrace", "︸": "underbrace", "⎵": "underbracket"}
_OVERS = {"⏞": "overbrace", "︷": "overbrace", "⎴": "overbracket"}
_VARIANTS = {"bold": "bold", "italic": "italic", "bold-italic": "bold", "double-struck": "bb", "script": "cal", "fraktur": "frak", "sans-serif": "sans", "monospace": "mono", "normal": "upright"}
# Characters with a meaning in Typst math markup; everything else is emitted as-is.
# Brackets are escaped too: MathML often splits a pair across subtrees ("(" as the base of an
# <msub>, ")" elsewhere), and an unbalanced bracket inside attach(...) breaks the call.
_SPECIAL = set("\\$#\"_^&/'*@<>[]{}()~%`,;:!|")


class Unsupported(Exception):
    pass


def _local(el: Any) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _text(el: Any) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _esc(text: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in text)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _token(text: str, kind: str) -> str:
    if not text:
        return ""
    if kind == "mi":
        if len(text) == 1:
            return text if text.isalpha() else _esc(text)
        if text in _FUNCTIONS:
            return f"op({_quote(text)})"
        return f"upright({_quote(text)})"
    if kind == "mn":
        return _esc(text)
    # mo: a word ("min", "lim") is one operator; otherwise single symbols, escaped where special
    if len(text) > 1 and text.isalpha():
        return f"op({_quote(text)})"
    return " ".join(_esc(c) if c in _SPECIAL else c for c in text)


class _Converter:
    def node(self, el: Any) -> str:
        tag = _local(el)
        handler = getattr(self, "_" + tag.replace("-", "_"), None)
        if handler is None:
            raise Unsupported(tag)
        return handler(el)

    def row(self, el: Any) -> str:
        return " ".join(p for p in (self.node(c) for c in el if _local(c)) if p)

    def arg(self, el: Any) -> str:
        """One argument of a math call; empty args become a zero-width space."""
        out = self.node(el)
        return out if out.strip() else "zws"

    # token elements
    def _mi(self, el: Any) -> str:
        variant = el.get("mathvariant")
        out = _token(_text(el), "mi")
        if variant in _VARIANTS and out:
            out = f"{_VARIANTS[variant]}({out})"
        return out

    def _mn(self, el: Any) -> str:
        return _token(_text(el), "mn")

    def _mo(self, el: Any) -> str:
        return _token(_text(el), "mo")

    def _mtext(self, el: Any) -> str:
        text = "".join(el.itertext())
        return _quote(text) if text.strip() else "space"

    def _ms(self, el: Any) -> str:
        return _quote('"' + _text(el) + '"')

    def _mspace(self, el: Any) -> str:
        return "space"

    # containers
    def _math(self, el: Any) -> str:
        return self.row(el)

    _mrow = _mstyle = _mpadded = _merror = _math

    def _mphantom(self, el: Any) -> str:
        return ""  # invisible spacing only (and `hide` is not callable in Typst math)

    def _menclose(self, el: Any) -> str:
        return self.row(el)

    def _semantics(self, el: Any) -> str:
        first = next((c for c in el if _local(c) and not _local(c).startswith("annotation")), None)
        if first is None:
            raise Unsupported("semantics without presentation")
        return self.node(first)

    # scripts and layout
    def _kids(self, el: Any, n: int) -> list[Any]:
        kids = [c for c in el if _local(c)]
        if len(kids) != n:
            raise Unsupported(f"{_local(el)} with {len(kids)} children")
        return kids

    def _msub(self, el: Any) -> str:
        base, sub = self._kids(el, 2)
        return f"attach({self.arg(base)}, b: {self.arg(sub)})"

    def _msup(self, el: Any) -> str:
        base, sup = self._kids(el, 2)
        return f"attach({self.arg(base)}, t: {self.arg(sup)})"

    def _msubsup(self, el: Any) -> str:
        base, sub, sup = self._kids(el, 3)
        return f"attach({self.arg(base)}, b: {self.arg(sub)}, t: {self.arg(sup)})"

    def _mfrac(self, el: Any) -> str:
        num, den = self._kids(el, 2)
        if el.get("linethickness") in ("0", "0pt", "0px"):
            return f"binom({self.arg(num)}, {self.arg(den)})"
        return f"frac({self.arg(num)}, {self.arg(den)})"

    def _msqrt(self, el: Any) -> str:
        return f"sqrt({self.row(el) or 'zws'})"

    def _mroot(self, el: Any) -> str:
        base, index = self._kids(el, 2)
        return f"root({self.arg(index)}, {self.arg(base)})"

    def _mover(self, el: Any) -> str:
        base, over = self._kids(el, 2)
        accent = _text(over)
        if _local(over) == "mo" and accent in _OVERS:
            return f"{_OVERS[accent]}({self.arg(base)})"
        if _local(over) == "mo" and accent in _ACCENTS:
            return f"{_ACCENTS[accent]}({self.arg(base)})"
        return f"attach(limits({self.arg(base)}), t: {self.arg(over)})"

    def _munder(self, el: Any) -> str:
        base, under = self._kids(el, 2)
        if _local(under) == "mo" and _text(under) in _UNDERS:
            return f"{_UNDERS[_text(under)]}({self.arg(base)})"
        return f"attach(limits({self.arg(base)}), b: {self.arg(under)})"

    def _munderover(self, el: Any) -> str:
        base, under, over = self._kids(el, 3)
        return f"attach(limits({self.arg(base)}), b: {self.arg(under)}, t: {self.arg(over)})"

    def _mfenced(self, el: Any) -> str:
        opening, closing = el.get("open", "("), el.get("close", ")")
        seps = (el.get("separators", ",") or "").split() or [","]
        sep = _esc(seps[0][:1] or ",")
        inner = f" {sep} ".join(self.node(c) for c in el if _local(c))
        return f"lr({_esc(opening)} {inner} {_esc(closing)})" if opening or closing else inner

    def _mtable(self, el: Any) -> str:
        rows = []
        for tr in el:
            if _local(tr) not in ("mtr", "mlabeledtr"):
                continue
            cells = [c for c in tr if _local(c) == "mtd"]
            rows.append(", ".join(self.row(c) or "zws" for c in cells))
        if not rows:
            raise Unsupported("empty mtable")
        return "mat(delim: #none, " + "; ".join(rows) + ")"


def validate(formulas: list[str]) -> list[bool]:
    """Which converted formulas Typst accepts. One compile when all are fine; failures are
    isolated by bisection. A single bad formula would otherwise break the whole PDF."""
    import typst

    def ok(idx: list[int]) -> bool:
        doc = "\n\n".join(f"${formulas[i]}$" for i in idx)
        try:
            typst.compile(doc.encode(), format="pdf", ignore_system_fonts=True)
            return True
        except Exception:  # noqa: BLE001 - any failure means "Typst rejects this formula"
            return False

    good = [True] * len(formulas)

    def check(idx: list[int]) -> None:
        if not idx or ok(idx):
            return
        if len(idx) == 1:
            good[idx[0]] = False
            return
        mid = len(idx) // 2
        check(idx[:mid])
        check(idx[mid:])

    check(list(range(len(formulas))))
    return good


def mathml_to_segments(mathml: str) -> list[str] | None:
    """The top-level pieces of a formula (terms, operators), each valid Typst math on its own,
    so a display equation too wide for its column can be broken into lines between them.
    Joined with spaces they equal mathml_to_typst(mathml)."""
    try:
        root = etree.fromstring(mathml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
        node = root
        while True:  # descend through single-child wrappers to the real top-level row
            kids = [c for c in node if _local(c) and not _local(c).startswith("annotation")]
            if len(kids) == 1 and _local(kids[0]) in ("mrow", "mstyle", "semantics", "mpadded"):
                node = kids[0]
            else:
                break
        conv = _Converter()
        segments = [s for s in (conv.node(c) for c in kids) if s.strip()]
    except (Unsupported, etree.XMLSyntaxError, RecursionError):
        return None
    return _break_points(segments) or None


_BREAK_BEFORE = {"=", "+", "−", "-", "×", "·", "≤", "≥", "\\<", "\\>", "≈", "∼", "≡", "∝", "→", "⇒", "⟹"}


def _break_points(segments: list[str]) -> list[str]:
    """Merge pieces so lines can only break before an operator outside brackets: flat MathML
    (PLOS) writes brackets as loose tokens, and a break inside them reads badly."""
    chunks: list[str] = []
    depth = 0
    for seg in segments:
        opens = seg.count("\\(") + seg.count("\\[") + seg.count("\\{")
        closes = seg.count("\\)") + seg.count("\\]") + seg.count("\\}")
        if chunks and not (depth == 0 and seg.strip() in _BREAK_BEFORE):
            chunks[-1] += " " + seg
        else:
            chunks.append(seg)
        depth = max(0, depth + opens - closes)
    return chunks if len(chunks) > 1 else segments


def mathml_to_typst(mathml: str) -> str | None:
    """Convert a serialized <math> element to Typst math source, or None if unsupported."""
    try:
        root = etree.fromstring(mathml.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
        out = _Converter().node(root).strip()
    except (Unsupported, etree.XMLSyntaxError, RecursionError):
        return None
    return out or None
