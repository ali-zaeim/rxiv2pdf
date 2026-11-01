"""Article -> Typst: escaping, citation collapsing, and every fixture compiling cleanly."""

import json
import string
from pathlib import Path

import pytest
import typst
from PIL import Image

from rxiv2pdf.compile import compile_typst, font_dirs, install_template
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.model import (
    DisplayMath,
    Figure,
    InlineGraphic,
    InlineMath,
    Paragraph,
    Section,
    TableBlock,
    Text,
    plain_text,
)
from rxiv2pdf.render_typst import (
    ImageRef,
    RenderOptions,
    collapse,
    escape,
    license_name,
    render,
)

FIXTURES = Path(__file__).parent / "fixtures"
XML_FILES = sorted(FIXTURES.glob("*/v*.source.xml"))

# Flattens parsed Typst content back to text so we can check what the parser saw.
TO_STR = r"""
#let to-str(it) = {
  if type(it) == str { it }
  else if it == none { "" }
  else if it.has("text") { it.text }
  else if it.has("children") { it.children.map(to-str).join() }
  else if it.has("body") { to-str(it.body) }
  else if it.func() == smartquote { if it.double { "\"" } else { "'" } }
  else if it.func() == [ ].func() { " " }
  else { "<" + repr(it.func()) + ">" }
}
"""


def typst_sees(markup: str) -> str:
    src = TO_STR + f"#let c = [{markup}]\n#metadata(to-str(c)) <out>\n"
    return json.loads(typst.query(src.encode(), "<out>", field="value", one=True))


# --- escaping ------------------------------------------------------------------------------
@pytest.mark.parametrize("ch", [c for c in string.punctuation if c not in "\"'"])
def test_every_ascii_punctuation_survives(ch):
    assert typst_sees(escape(f"a{ch}b {ch}c")) == f"a{ch}b {ch}c"


@pytest.mark.parametrize(
    "text",
    [
        "1. Introduction",  # would become an enum item
        "- not a list",
        "+ not an enum",
        "= not a heading",
        "/ term: not a term list",
        "see http://example.org//path and a /* not a comment */",
        "a -- b --- c ... d ~ e -> f",
        "#let x = 1; $math$ *bold* _emph_ `raw` @ref <label> [brackets] \\backslash",
        "(starts with paren) and .starts with dot",
        "Unicode: αβγ Δ → ≤ Øyvind Müller 张三 – — …",
    ],
)
def test_markup_hazards_round_trip(text):
    assert typst_sees(escape(text)) == text


def test_escape_collapses_whitespace_and_never_emits_newlines():
    assert escape("a\n  b\tc") == "a b c"


def test_text_after_embedded_call_cannot_continue_it():
    # "#emph[x]" followed by ".y" or "(z)" must not become field access / call args.
    assert typst_sees("#emph[x]" + escape(".y") + "#emph[x]" + escape("(z)")) == "x.yx(z)"


# --- helpers -------------------------------------------------------------------------------
def test_collapse_runs():
    assert collapse([1, 2, 3, 4, 7, 9, 10]) == [(1, 4), (7, 7), (9, 10)]
    assert collapse([5]) == [(5, 5)]
    assert collapse([3, 1, 2]) == [(3, 3), (1, 2)]  # source order kept
    assert collapse([1, 2, 2, 3]) == [(1, 3)]


def test_license_names():
    assert license_name("cc_by_nc_nd", "http://creativecommons.org/licenses/by-nc-nd/4.0/") == "CC BY-NC-ND 4.0"
    assert license_name("cc_by", None) == "CC BY 4.0"
    assert license_name("cc0", "https://creativecommons.org/publicdomain/zero/1.0/") == "CC0 1.0"
    assert license_name("cc_no", None) == "No reuse allowed without permission"
    assert license_name(None, None) is None


# --- compiling ------------------------------------------------------------------------------
def graphic_hrefs(article):
    def walk(blocks):
        for b in blocks:
            yield b
            if isinstance(b, Section):
                yield from walk(b.blocks)

    hrefs = []
    for b in walk(article.abstract + article.body + article.back):
        if isinstance(b, Figure | TableBlock):
            hrefs += b.graphics
        if isinstance(b, DisplayMath) and b.image:
            hrefs.append(b.image)
        for n in getattr(b, "content", []):
            if isinstance(n, InlineMath) and n.image:
                hrefs.append(n.image)
            if isinstance(n, InlineGraphic):
                hrefs.append(n.href)
    return hrefs


def compile_article(article, tmp_path, assets=None, options=None):
    if assets is None:
        (tmp_path / "figures").mkdir(exist_ok=True)
        assets = {}
        for href in graphic_hrefs(article):
            rel = f"figures/{href.replace('/', '_')}.png"
            Image.new("RGB", (40, 30), (120, 120, 200)).save(tmp_path / rel)
            assets[href] = ImageRef(rel, 40, 30)
    source, render_warnings = render(article, assets=assets, options=options)
    install_template(tmp_path)
    (tmp_path / "doc.typ").write_text(source, encoding="utf-8")
    warnings = compile_typst(tmp_path / "doc.typ", tmp_path / "doc.pdf", root=tmp_path)
    return source, render_warnings, warnings


@pytest.mark.parametrize("xml", XML_FILES, ids=lambda p: p.parent.name)
def test_every_fixture_compiles_without_errors_or_warnings(xml, tmp_path):
    article = parse_jats(xml.read_bytes())
    source, render_warnings, warnings = compile_article(article, tmp_path)
    assert render_warnings == [] and warnings == []
    assert (tmp_path / "doc.pdf").read_bytes().startswith(b"%PDF")
    assert '#import "journal.typ": *' in source


def test_citations_render_as_collapsed_segments(tmp_path):
    article = parse_jats(next(FIXTURES.glob("2024.04.17.590000/*.xml")).read_bytes())
    source, _, _ = compile_article(article, tmp_path)
    assert 'ion channels#cites(((1, 5, "ref-c1", "ref-c5"),))' in source  # <sup>1–5</sup>
    assert "#ref-entry(1, [Jasti, J." in source and "<ref-c1>" in source


@pytest.mark.parametrize(
    ("options", "expect"),
    [
        (RenderOptions(paper="a4", cite_style="brackets"), ['paper: "a4"', 'cite-style: "brackets"']),
        (RenderOptions(), ['paper: "us-letter"', "cite-style: none", '#import "themes/default.typ"']),
    ],
)
def test_options_compile(options, expect, tmp_path):
    article = parse_jats(next(FIXTURES.glob("2026.10.16.759679/*.xml")).read_bytes())
    source, _, warnings = compile_article(article, tmp_path, options=options)
    assert warnings == [] and all(e in source for e in expect)


@pytest.mark.parametrize("theme", ["default", "compact", "sans"])
@pytest.mark.parametrize("fixture", ["2024.04.17.590000", "2026.09.25.754430", "2026.10.02.26364566"])
def test_every_theme_compiles_cleanly(theme, fixture, tmp_path):
    article = parse_jats(next(FIXTURES.glob(f"{fixture}/*.xml")).read_bytes())
    source, render_warnings, warnings = compile_article(article, tmp_path, options=RenderOptions(theme=theme))
    assert f'#import "themes/{theme}.typ": theme as rxiv-theme' in source
    assert render_warnings == [] and warnings == []


def test_theme_settings_take_effect(tmp_path):
    """Query the compiled document: headings numbered only in the default theme."""
    article = parse_jats(next(FIXTURES.glob("2026.10.16.759679/*.xml")).read_bytes())
    numbering = {}
    for theme in ("default", "compact", "sans"):
        compile_article(article, tmp_path, options=RenderOptions(theme=theme))
        heads = json.loads(typst.query(str(tmp_path / "doc.typ"), "heading", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True))
        numbering[theme] = heads[0]["numbering"]
    assert numbering == {"default": "1.1", "compact": None, "sans": None}
    pdf = (tmp_path / "doc.pdf").read_bytes()  # last compiled: sans theme, sans body
    assert b"SourceSans3-Regular" in pdf


def test_subsection_title_runs_into_its_first_paragraph(tmp_path):
    """No paragraph break after a subsection title, so `compact` can set it run-in."""
    article = parse_jats(next(FIXTURES.glob("2026.10.16.759679/*.xml")).read_bytes())
    source, _ = render(article, assets={})
    assert "== Mice <sec-s2b>\nThe 5XFAD mice" in source
    assert "= Materials and methods <sec-s2>\n\n== Study design" in source  # level 1: always a block
    compile_article(article, tmp_path, options=RenderOptions(theme="compact"))
    heads = json.loads(typst.query(str(tmp_path / "doc.typ"), "heading", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True))
    assert any(h["body"]["text"] == "Mice" for h in heads if h["level"] == 2)  # still a heading (PDF outline)


def test_unknown_theme_is_rejected():
    article = parse_jats(next(FIXTURES.glob("2026.10.16.759679/*.xml")).read_bytes())
    with pytest.raises(ValueError, match="Unknown theme 'elsevier'"):
        render(article, assets={}, options=RenderOptions(theme="elsevier"))


def test_missing_images_render_placeholders(tmp_path):
    article = parse_jats(next(FIXTURES.glob("2020.03.22.002386/*.xml")).read_bytes())
    source, render_warnings, warnings = compile_article(article, tmp_path, assets={})
    assert "images: ()" in source and warnings == []
    assert any("Figure 1: image 002386v3_fig1 is missing" in w for w in render_warnings)


def test_xrefs_link_only_to_labels_that_exist(tmp_path):
    from test_jats import jats

    article = parse_jats(jats(body=(
        '<sec><title>S</title><p>See <xref ref-type="fig" rid="f1">Fig. 1</xref> and '
        '<xref ref-type="fig" rid="nope">Fig. 9</xref>.</p>'
        '<fig id="f1"><label>Figure 1.</label><caption><title>Cap</title></caption></fig></sec>'
    )))
    source, _, warnings = compile_article(article, tmp_path)
    assert '#link(label("fig-f1"))[Fig. 1]' in source and "<fig-f1>" in source
    assert "and Fig. 9\\." in source  # dangling target: plain text (no link), no compile error
    assert warnings == []


# --- journal template -----------------------------------------------------------------------
def test_caption_paragraphs_join_with_a_period_when_unpunctuated():
    from rxiv2pdf.render_typst import _run_in

    out = _run_in([Paragraph([Text("W: Wake")]), Paragraph([Text("Note: all.")]), Paragraph([Text("(B) Panel (n = 3)")]),
                   Paragraph([Text("Abbreviations:")]), Paragraph([Text("TST.")])])
    assert plain_text(out[0].content) == "W: Wake. Note: all. (B) Panel (n = 3). Abbreviations: TST."


def test_sequences_are_not_hyphenated_and_kets_hold_together(tmp_path):
    from test_jats import jats

    from rxiv2pdf.render_typst import _text

    assert _text("primer ACCTATTCCGAGCCATTGACC, next") == "primer #text(hyphenate: false)[ACCTATTCCGAGCCATTGACC], next"
    assert _text("ACGT" * 10).count("\u200b") == 3  # > 30 bases: may break (no hyphen) every 10
    assert _text("CATCAT and ACGTACGTACGTA.") == "CATCAT and #text(hyphenate: false)[ACGTACGTACGTA]\\."
    assert _text("GAPDH and CATCATCATCATxyz") == "GAPDH and CATCATCATCATxyz"  # part of a longer word
    # protein sequences (2025.08.14.670383: a 300-residue construct ran off the page)
    protein = "MGTDSLEFIASKLAHHHHHHWGSGHGVGVPGMGVPGVGVPG"
    assert _text(protein) == "#text(hyphenate: false)[" + "\u200b".join(protein[i : i + 10] for i in range(0, 41, 10)) + "]"
    assert _text("INTRODUCTION AND METHODS") == "INTRODUCTION AND METHODS"  # capitals, but words
    assert _text("ground state |") == "ground state |\u2060" and _text("a | b") == "a | b"
    assert typst_sees(_text("(primer ACCTATTCCGAGCCATTGACC) |R")) .endswith("|\u2060R")
    article = parse_jats(jats(body="<sec><title>S</title><p>Primers ACCTATTCCGAGCCATTGACC and |<italic>R</italic>⟩.</p></sec>"))
    _, render_warnings, warnings = compile_article(article, tmp_path)
    assert render_warnings == [] and warnings == []


def test_supplementary_files_link_to_the_server(tmp_path):
    from test_jats import jats

    article = parse_jats(jats(body=(
        '<sec><title>Supplementary</title><supplementary-material id="s1"><label>Data S1</label>'
        '<media xlink:href="supplements/x_file01.xlsx"/></supplementary-material></sec>'
    )))
    url = "https://www.biorxiv.org/content/biorxiv/early/2025/01/01/2025.01.01.000001/DC1/embed/media-1.xlsx"
    source, _ = render(article, assets={}, supplement_urls={"supplements/x_file01.xlsx": url})
    assert f'file: "x_file01.xlsx", url: "{url}"' in source
    assert 'url: none' in render(article, assets={})[0]
    _, render_warnings, warnings = compile_article(article, tmp_path)
    assert render_warnings == [] and warnings == []


def test_pdf_page_count(tmp_path):
    from rxiv2pdf.compile import pdf_page_count

    (tmp_path / "five.pdf").write_bytes(typst.compile(b"1 #pagebreak() 2 #pagebreak() 3 #pagebreak() 4 #pagebreak() 5"))
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 not really")
    assert pdf_page_count(tmp_path / "five.pdf") == 5
    assert pdf_page_count(tmp_path / "bad.pdf") is None


def test_short_title_truncates_at_word_boundary():
    from rxiv2pdf.render_typst import short_title

    assert short_title("Short title") == "Short title"
    long = "Molecular basis of proton-sensing by G protein-coupled receptors in mammals"
    out = short_title(long)
    assert out == "Molecular basis of proton-sensing by G protein-coupled…" and len(out) <= 61


def test_running_author():
    from test_jats import jats

    from rxiv2pdf.render_typst import running_author

    a = parse_jats(jats())
    assert running_author(a) == "Doe et al."
    a.authors = a.authors[:1]
    assert running_author(a) == "Doe"
    a.authors = [parse_jats(jats()).authors[1]]
    assert running_author(a) == "The Big Consortium"


def test_bundled_fonts_are_packaged_and_embedded(tmp_path):
    from rxiv2pdf.compile import font_dirs

    (fonts,) = font_dirs()
    names = {p.name for p in Path(fonts).iterdir()}
    assert {"SourceSerif4SmText-Regular.ttf", "SourceSans3-Bold.ttf", "OFL-SourceSerif4.md", "OFL-SourceSans3.md"} <= names
    from test_jats import jats

    # Ordinary text (Latin, Greek, common symbols) never falls back to Typst's built-in
    # Libertinus. Rarer symbols may: 2026.10.16.759679 has "⁺" (U+207A) and "∼" (U+223C).
    article = parse_jats(jats(body="<sec><title>Results</title><p>Mice (<italic>n</italic> = 12) "
                                   "showed α-synuclein levels ≥ 2.5 µM ± 0.3 at 37 °C — “quoted” text.</p></sec>"))
    compile_article(article, tmp_path)
    pdf = (tmp_path / "doc.pdf").read_bytes()
    assert b"SourceSerif4SmText" in pdf and b"SourceSans3" in pdf
    assert b"Libertinus" not in pdf


def test_paragraph_resuming_after_display_element_is_not_indented(tmp_path):
    article = parse_jats(next(FIXTURES.glob("2026.10.22.760766/*.xml")).read_bytes())
    source, _, warnings = compile_article(article, tmp_path)
    assert "#cont-par[where #emph[I] denotes phosphosite abundance" in source
    assert warnings == []


def test_author_year_papers_get_unnumbered_reference_list(tmp_path):
    from test_jats import REFS, jats

    article = parse_jats(jats(body='<p>Shown (<xref ref-type="bibr" rid="r2">Jones, 2019</xref>).</p>', back=REFS))
    source, _, warnings = compile_article(article, tmp_path)
    assert article.cite_mode == "author-year" and warnings == []
    assert "#ref-entry(none, [Two.]) <ref-r2>" in source
    assert '#link(label("ref-r2"))[Jones, 2019]' in source


def test_collapse_refs_keeps_ids_for_links():
    from rxiv2pdf.render_typst import collapse_refs

    pairs = [(1, "c1"), (2, "c2"), (3, "c3"), (7, "c7"), (9, "c9"), (10, "c10")]
    assert collapse_refs(pairs) == [(1, 3, "c1", "c3"), (7, 7, "c7", "c7"), (9, 10, "c9", "c10")]


def test_two_reference_lists_each_list_their_own_entries(tmp_path):
    """2026.10.06.756929 (acceptance run): main references c1..c105 plus a supplementary list
    sc1..sc3 numbered from 1 again. Typst used to fail with duplicate labels."""
    from test_jats import jats

    back = (
        '<ref-list><title>References</title><ref id="c1"><label>1</label><mixed-citation>Main one.</mixed-citation></ref>'
        '<ref id="c2"><label>2</label><mixed-citation>Main two.</mixed-citation></ref></ref-list>'
        '<sec><title>Supplementary</title><p>Supp cites<sup><xref ref-type="bibr" rid="sc1">1</xref></sup>.</p></sec>'
        '<ref-list><title>References</title><ref id="sc1"><label>1</label><mixed-citation>Supp one.</mixed-citation></ref></ref-list>'
    )
    body = '<sec><title>S</title><p>Main cites<sup><xref ref-type="bibr" rid="c1">1</xref>,<xref ref-type="bibr" rid="c2">2</xref></sup>.</p></sec>'
    article = parse_jats(jats(body=body, back=back))
    assert [(r.id, r.number) for r in article.references] == [("c1", 1), ("c2", 2), ("sc1", 1)]
    source, _, warnings = compile_article(article, tmp_path)
    assert source.count("<ref-c1>") == 1 and source.count("<ref-sc1>") == 1 and source.count("#ref-entry(") == 3
    assert '#cites(((1, 2, "ref-c1", "ref-c2"),))' in source and '#cites(((1, 1, "ref-sc1", "ref-sc1"),))' in source
    assert warnings == []
