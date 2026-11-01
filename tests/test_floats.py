"""Figure/table placement, spanning rules, XML tables (booktabs)."""

import json

import pytest
import typst
from PIL import Image
from test_jats import REFS, jats

from rxiv2pdf.compile import (
    REF_SCALE_INPUT,
    _balance_last_page,
    _pull_back_short_tail,
    compile_typst,
    font_dirs,
    install_template,
    template_dir,
)
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.render_typst import ImageRef, render


def fig(fid, label, words=10, graphic=None):
    cap = " ".join(["word"] * words)
    g = f'<graphic xlink:href="{graphic or fid}"/>'
    return f'<fig id="{fid}"><label>{label}</label><caption><title>T</title><p>{cap}</p></caption>{g}</fig>'


def build(tmp_path, body, back="", sizes=None, text_px=None):
    """Render + compile; images get the given pixel sizes (default 800x600) and, for table
    screenshots, the given text line heights."""
    article = parse_jats(jats(body=body, back=back))
    (tmp_path / "figures").mkdir(exist_ok=True)
    assets = {}
    for href, (w, h) in (sizes or {}).items():
        Image.new("RGB", (w, h), (90, 120, 200)).save(tmp_path / f"figures/{href}.png")
        assets[href] = ImageRef(f"figures/{href}.png", w, h, (text_px or {}).get(href))
    source, render_warnings = render(article, assets=assets)
    install_template(tmp_path)
    (tmp_path / "doc.typ").write_text(source, encoding="utf-8")
    warnings = compile_typst(tmp_path / "doc.typ", tmp_path / "doc.pdf", root=tmp_path)
    return article, source, render_warnings, warnings


def tail(source):
    """The `tail:` argument of article.with(...): floats set on pages of their own after the text."""
    start = source.index("\n  tail: [")
    return source[start : source.index("\n)\n", start)]


def floats(tmp_path):
    """Scope ("column"/"parent") of every floating figure/table, in document order."""
    raw = typst.query(str(tmp_path / "doc.typ"), "figure", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True)
    return [f["scope"] for f in json.loads(raw) if f.get("placement") not in (None, "none")]


# --- placement --------------------------------------------------------------------------------
def test_figure_moves_to_after_its_first_citation(tmp_path):
    body = (
        "<sec><title>S</title>"
        + fig("f1", "Figure 1")  # appears BEFORE it is cited
        + '<p>Intro text.</p><p>As shown in <xref ref-type="fig" rid="f1">Fig. 1</xref>.</p><p>More text.</p>'
        + '<p>Mention <xref ref-type="fig" rid="f2">Fig. 2</xref> early.</p><p>A</p><p>B</p><p>C</p>'
        + fig("f2", "Figure 2")  # far AFTER its first citation
        + fig("f3", "Figure 3")  # never cited: stays put
        + "</sec>"
    )
    _, source, _, warnings = build(tmp_path, body, sizes={"f1": (800, 600), "f2": (800, 600), "f3": (800, 600)})
    order = [source.index(m) for m in ("Intro text", "As shown in", "lbl: [Figure 1]", "More text", "Mention",
                                       "lbl: [Figure 2]", "\nA\n", "lbl: [Figure 3]")]
    assert order == sorted(order) and warnings == []
    assert source.count("lbl: [Figure 1]") == 1  # moved, not duplicated


def test_figures_stay_in_number_order(tmp_path):
    # Fig. 2 is mentioned before Fig. 1 (seen: "see Figure 5" in a Methods section).
    body = (
        '<sec><title>S</title><p>Early <xref ref-type="fig" rid="f2">Fig. 2</xref>.</p><p>Middle.</p>'
        '<p>Then <xref ref-type="fig" rid="f1">Fig. 1</xref>.</p><p>End.</p>'
        + fig("f1", "Figure 1") + fig("f2", "Figure 2") + "</sec>"
    )
    _, source, _, _ = build(tmp_path, body, sizes={"f1": (800, 600), "f2": (800, 600)})
    order = [source.index(m) for m in ("Early", "Middle.", "Then", "lbl: [Figure 1]", "lbl: [Figure 2]", "End.")]
    assert order == sorted(order)


def test_column_float_that_would_run_off_the_page_becomes_page_wide(tmp_path):
    # Typst 0.15 moves a column float to the right column under a page-wide float without
    # checking that it fits there: the tall Fig. 2 ran into the bottom margin and its caption
    # off the page. journal.typ detects that and makes the float page-wide.
    body = (
        '<sec><title>S</title><p>See <xref ref-type="fig" rid="f1">Fig. 1</xref> and '
        '<xref ref-type="fig" rid="f2">Fig. 2</xref>.</p>' + fig("f1", "Figure 1") + fig("f2", "Figure 2")
        + "<p>" + " ".join(["filler"] * 900) + "</p></sec>"
    )
    _, _, _, warnings = build(tmp_path, body, sizes={"f1": (1600, 1000), "f2": (600, 1100)})
    assert warnings == []  # incl. no "did not converge"
    assert floats(tmp_path) == ["parent", "parent"]


def test_table_screenshots_are_sized_by_their_text_and_slices_rejoined(tmp_path):
    def table(tid, *hrefs):
        graphics = "".join(f'<graphic xlink:href="{h}"/>' for h in hrefs)
        return f'<table-wrap id="{tid}"><label>Table {tid[1:]}</label><caption><title>T</title></caption>{graphics}</table-wrap>'

    body = (
        '<sec><title>S</title><p>See <xref ref-type="table" rid="t1">Table 1</xref>, '
        '<xref ref-type="table" rid="t2">Table 2</xref> and <xref ref-type="table" rid="t3">Table 3</xref>.</p>'
        + table("t1", "t1a", "t1b") + table("t2", "t2a") + table("t3", "t3a") + "</sec>"
    )
    sizes = {"t1a": (1280, 300), "t1b": (1280, 200), "t2a": (1280, 300), "t3a": (1280, 300)}
    # t1: large type in the image -> scaled down to a column, both slices in one float;
    # t2: small type -> full width; t3: unknown -> full width as before.
    _, source, _, warnings = build(tmp_path, body, sizes=sizes, text_px={"t1a": 60, "t1b": 60, "t2a": 15})
    assert warnings == [] and "sizes: ((1280, 300, 60), (1280, 200, 60), )" in source
    assert floats(tmp_path) == ["column", "parent", "parent"]


def test_citation_inside_a_caption_does_not_move_a_figure(tmp_path):
    body = (
        '<sec><title>S</title><p>Text <xref ref-type="fig" rid="f1">Fig. 1</xref>.</p>'
        '<fig id="f1"><label>Figure 1</label><caption><p>See also <xref ref-type="fig" rid="f2">Fig. 2</xref>.</p></caption>'
        '<graphic xlink:href="f1"/></fig><p>Middle.</p>' + fig("f2", "Figure 2") + "</sec>"
    )
    _, source, _, _ = build(tmp_path, body, sizes={"f1": (800, 600), "f2": (800, 600)})
    assert source.index("Middle.") < source.index("lbl: [Figure 2]")


def test_back_matter_extended_data_keeps_its_position(tmp_path):
    body = '<sec><title>S</title><p>See <xref ref-type="fig" rid="x1">Extended Data Fig. 1</xref>.</p></sec>'
    back = REFS + '<sec><title>Extended data</title>' + fig("x1", "Extended Data Fig. 1") + "</sec>"
    _, source, _, _ = build(tmp_path, body, back=back, sizes={"x1": (800, 600)})
    # not moved up to its citation: after all the text (pages of its own), with its section's heading
    assert "lbl: [Extended Data Fig. 1]" in tail(source) and source.count("lbl: [Extended Data Fig. 1]") == 1
    assert "header: [= Extended data]" in source.split("lbl: [Extended Data Fig. 1]")[1].split("\n")[0]
    assert '#link(label("fig-x1"))' in source


# --- width rule --------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("size", "words", "scope"),
    [
        ((1400, 1000), 20, "parent"),  # aspect 1.4 > 1.3: span
        ((1200, 1000), 20, "column"),  # aspect 1.2, short caption: one column
        ((600, 1000), 120, "parent"),  # tall, but caption > 80 words: span
        ((600, 1000), 60, "column"),
    ],
)
def test_width_rule(tmp_path, size, words, scope):
    _, source, _, warnings = build(tmp_path, "<sec><title>S</title>" + fig("f1", "Figure 1", words) + "</sec>", sizes={"f1": size})
    assert floats(tmp_path) == [scope] and warnings == []
    assert f"aspect: {size[0] / size[1]:.3f}" in source


@pytest.mark.parametrize(
    ("glyph_px", "scope", "width_pt"),
    [
        (None, "parent", None),  # no lettering found: the shape rule (aspect 1.4 > 1.3) as before
        (12, "parent", None),  # fine lettering: printed full width it stays below 12 pt
        (40, "column", 1280 * 0.53 * 12 / 40),  # a simple chart: capped by its lettering, one column
        (200, "column", None),  # lettering cap under half a column: half a column (checked below)
    ],
)
def test_figure_width_capped_by_its_lettering(tmp_path, glyph_px, scope, width_pt):
    # 2025.07.21.665831 Fig. 5, 2025.08.24.671960 Supp. Fig. 2: bar/box charts ~1280 px wide with
    # 35-45 px lettering printed full width = 16-27 pt type.
    _, source, _, warnings = build(tmp_path, "<sec><title>S</title>" + fig("f1", "Figure 1") + "</sec>",
                                   sizes={"f1": (1280, 914)}, text_px={"f1": glyph_px})
    assert floats(tmp_path) == [scope] and warnings == []
    assert f"sizes: ((1280, 914, {'none' if glyph_px is None else glyph_px}), )" in source
    raw = typst.query(str(tmp_path / "doc.typ"), "image", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True)
    printed = float(json.loads(raw)[0]["width"].rsplit("+", 1)[-1].strip().removesuffix("pt"))  # "0% + 203.5pt"
    col = ((215.9 - 36) / 25.4 * 72 - 5 / 25.4 * 72) / 2
    if width_pt is not None:
        assert printed == pytest.approx(width_pt, abs=0.5)
    elif glyph_px == 200:
        assert printed == pytest.approx(col / 2, abs=0.5)  # never under half a column
    else:
        assert printed > col  # full width


def test_huge_caption_and_tall_image_still_compile(tmp_path):
    # Corpus maximum is ~900 words; the image shrinks so image + caption fit one page.
    _, _, _, warnings = build(tmp_path, "<sec><title>S</title>" + fig("f1", "Figure 1", 900) + "</sec>", sizes={"f1": (600, 1400)})
    assert warnings == [] and floats(tmp_path) == ["parent"]


def test_missing_image_figure_is_a_placeholder_float(tmp_path):
    _, source, render_warnings, warnings = build(tmp_path, "<sec><title>S</title>" + fig("f1", "Figure 1") + "</sec>")
    assert "images: (), sizes: (), aspect: none" in source and floats(tmp_path) == ["column"]
    assert render_warnings and warnings == []


# --- tables -------------------------------------------------------------------------------------
def test_multi_image_table_is_one_spanning_float_per_slice(tmp_path):
    tw = ('<table-wrap id="t1"><label>Table 1a.</label><caption><title>Drugs</title></caption>'
          + "".join(f'<graphic xlink:href="t1_{i}"/>' for i in range(3)) + "</table-wrap>")
    sizes = {f"t1_{i}": (1280, 1200) for i in range(3)}
    _, _, _, warnings = build(tmp_path, f"<sec><title>S</title>{tw}</sec>", sizes=sizes)
    assert floats(tmp_path) == ["parent"] * 3 and warnings == []


XML_TABLE = """<table-wrap id="t2"><label>Table 2.</label><caption><title>Expression</title></caption>
<table><thead><tr><th rowspan="2">Gene</th><th colspan="2">Expression</th></tr><tr><th>WT</th><th>KO</th></tr></thead>
<tbody>{rows}</tbody></table><table-wrap-foot><fn><p><sup>a</sup>Not detected.</p></fn></table-wrap-foot></table-wrap>"""


def test_xml_table_booktabs_with_spans(tmp_path):
    rows = '<tr><td>Abc1</td><td align="right">1.2</td><td>3.4</td></tr><tr><td>Def<sup>a</sup></td><td colspan="2">n.d.</td></tr>'
    article, source, _, warnings = build(tmp_path, "<sec><title>S</title>" + XML_TABLE.format(rows=rows) + "</sec>")
    t = article.body[0].blocks[0].table
    assert (t.ncols, len(t.head), len(t.body), t.malformed) == (3, 2, 2, False)
    assert "table.cell(rowspan: 2)[Gene]" in source and "table.cell(colspan: 2)[Expression]" in source
    assert "table.cell(align: right)[1\\.2]" in source and "nhead: 2" in source
    assert warnings == [] and floats(tmp_path) == ["column"]  # narrow: fits one column


def test_wide_xml_table_spans(tmp_path):
    cols = 9
    head = "".join(f"<th>Condition {i}</th>" for i in range(cols))
    row = "".join(f"<td>{i}.123456</td>" for i in range(cols))
    tw = f'<table-wrap id="t3"><label>Table 3.</label><table><thead><tr>{head}</tr></thead><tbody>{row * 5}</tbody></table></table-wrap>'
    tw = tw.replace(row * 5, "".join(f"<tr>{row}</tr>" for _ in range(5)))
    _, _, _, warnings = build(tmp_path, f"<sec><title>S</title>{tw}</sec>")
    assert floats(tmp_path) == ["parent"] and warnings == []


def test_long_xml_table_becomes_full_width_chunks_with_header(tmp_path):
    rows = "".join(f"<tr><td>Gene{i}</td><td>{i}</td><td>{i * 2}</td></tr>" for i in range(300))
    _, _, _, warnings = build(tmp_path, "<sec><title>S</title>" + XML_TABLE.format(rows=rows) + "</sec>")
    chunks = floats(tmp_path)
    assert len(chunks) >= 3 and set(chunks) == {"parent"} and warnings == []


def test_row_groups_keep_rowspans_together():
    from rxiv2pdf.model import TableCell
    from rxiv2pdf.render_typst import _row_groups

    c = lambda rowspan=1: TableCell([], rowspan=rowspan)
    rows = [[c(), c()], [c(rowspan=3), c()], [c()], [c()], [c(), c()]]
    assert [len(g) for g in _row_groups(rows)] == [1, 3, 1]


def test_malformed_xml_table_falls_back_to_image(tmp_path):
    tw = ('<table-wrap id="t4"><label>Table 4.</label><table><tr><td>a</td><td>b</td></tr><tr><td>only one</td></tr></table>'
          '<graphic xlink:href="t4img"/></table-wrap>')
    article, source, _, warnings = build(tmp_path, f"<sec><title>S</title>{tw}</sec>", sizes={"t4img": (1280, 600)})
    assert article.body[0].blocks[0].table.malformed
    assert "xml: none" in source and '"figures/t4img.png"' in source and warnings == []
    assert any("ragged rows" in w for w in article.warnings)


# --- long tables go to the end of the article ---------------------------------------------------
def image_table(n, size=(1280, 1200)):
    tw = ('<table-wrap id="t1"><label>Table 1a.</label><caption><title>Drugs</title></caption>'
          + "".join(f'<graphic xlink:href="t1_{i}"/>' for i in range(n)) + "</table-wrap>")
    body = f'<sec><title>S</title><p>See <xref ref-type="table" rid="t1">Table 1a</xref>.</p>{tw}<p>After the table.</p></sec>'
    return body, {f"t1_{i}": size for i in range(n)}


def test_two_page_image_table_stays_in_place(tmp_path):
    body, sizes = image_table(2)  # ~1.4 pages
    _, source, _, warnings = build(tmp_path, body, back=REFS, sizes=sizes)
    assert "deferred-note" not in source
    assert source.index("lbl: [Table 1a]") < source.index("After the table") < source.index("= References")
    assert warnings == []


def test_long_image_table_moves_to_end_with_linked_note(tmp_path):
    body, sizes = image_table(5)  # ~3.7 pages on US Letter
    _, source, _, warnings = build(tmp_path, body, back=REFS, sizes=sizes)
    note = '#deferred-note(lbl: [Table 1a], target: "tab-t1", pages: 4)'
    assert source.index("See #link") < source.index(note) < source.index("After the table")
    assert "#table-block(lbl: [Table 1a]" in tail(source)  # after the references, on pages of its own
    assert source.count("#table-block(") == 1 and '#link(label("tab-t1"))[Table 1a]' in source
    assert floats(tmp_path) == ["parent"] * 5 and warnings == []


def test_long_xml_table_moves_to_end(tmp_path):
    rows = "".join(f"<tr><td>Gene{i}</td><td>{i}</td><td>{i * 2}</td></tr>" for i in range(300))
    body = '<sec><title>S</title><p>See <xref ref-type="table" rid="t2">Table 2</xref>.</p>' + XML_TABLE.format(rows=rows) + "</sec>"
    _, source, _, warnings = build(tmp_path, body, back=REFS)
    assert "#deferred-note(lbl: [Table 2]" in source
    assert "#table-block(lbl: [Table 2]" in tail(source) and warnings == []


def test_text_area_constants_match_template_margins():
    from rxiv2pdf.render_typst import TEXT_AREA_MM

    template = (template_dir() / "journal.typ").read_text(encoding="utf-8")
    assert "margin: (x: 18mm, top: 20mm, bottom: 20mm)" in template
    assert "float-max: 0.94" in template
    assert TEXT_AREA_MM["us-letter"] == pytest.approx((215.9 - 36, 279.4 - 40))


def test_caption_paragraphs_run_in(tmp_path):
    # 2026.10.22.760766: one <p> per panel inside the caption.
    fig_xml = ('<fig id="f1"><label>Fig. 1</label><caption><title>T.</title><p><bold>(A)</bold> First.</p>'
               '<p><bold>(B)</bold> Second.</p><list><list-item><p>a list stays a block</p></list-item></list>'
               '<p>After list.</p></caption><graphic xlink:href="f1"/></fig>')
    _, source, _, warnings = build(tmp_path, f"<sec><title>S</title>{fig_xml}</sec>", sizes={"f1": (800, 600)})
    assert "caption: [#strong[\\(A)] First. #strong[\\(B)] Second.\n\n#list(" in source
    assert warnings == []


def test_rowspan_running_past_the_table_is_clipped_not_malformed(tmp_path):
    # PMC11753677 (PLOS Comput Biol), table 1: the last-but-one row has rowspan="3".
    tw = ('<table-wrap id="t5"><label>Table 5.</label><table><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead>'
          '<tbody><tr><td>1</td><td>2</td><td>3</td></tr><tr><td rowspan="3">x</td><td>4</td><td>5</td></tr>'
          '<tr><td>6</td><td>7</td></tr></tbody></table></table-wrap>')
    article, source, _, warnings = build(tmp_path, f"<sec><title>S</title>{tw}</sec>")
    table = article.body[0].blocks[0].table
    assert not table.malformed and table.body[1][0].rowspan == 2 and article.warnings == []
    assert "table.cell(rowspan: 2)[x]" in source and warnings == []


# --- last page ----------------------------------------------------------------------------------
def _refs(n):
    return "<ref-list><title>References</title>" + "".join(
        f'<ref id="r{i}"><label>{i}</label><mixed-citation>Author A, Author B. A rather long reference title, '
        f"number {i}. Journal of Things. 2020;{i}:1-10.</mixed-citation></ref>" for i in range(1, n + 1)) + "</ref-list>"


def _end_before_after(tmp_path, words, nrefs, step):
    """Compile a filler body + `nrefs` references, run a compile.py last-page step on it, and
    return the end of the body (journal.typ's <rxiv-end>) before and after, and its result."""
    body = "<sec><title>S</title><p>" + " ".join(["filler"] * words) + "</p></sec>"
    source, _ = render(parse_jats(jats(body=body, back=_refs(nrefs))), assets={})
    install_template(tmp_path)
    (tmp_path / "doc.typ").write_text(source, encoding="utf-8")
    compiler = typst.Compiler(str(tmp_path / "doc.typ"), root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True)
    compiler.compile_with_warnings(output=str(tmp_path / "doc.pdf"), sys_inputs={})
    before = json.loads(compiler.query("<rxiv-end>", field="value"))[0]
    result = step(compiler, tmp_path / "doc.pdf", {})
    after = json.loads(compiler.query("<rxiv-end>", field="value"))[0]
    return before, result, after


def test_last_page_columns_are_balanced(tmp_path):
    before, balanced, after = _end_before_after(tmp_path, 1500, 60, _balance_last_page)
    assert (before["page"], before["col"]) == (3, 0)  # the references end half-way down the left column
    assert balanced and (after["page"], after["col"]) == (3, 1)  # ... and now in the right one,
    assert abs(after["used"] - before["used"] / 2) < 1.5 * after["pitch"]  # at half the height
    assert sorted(p.name for p in tmp_path.glob("*.pdf")) == ["doc.pdf"]  # no trial file left


def test_short_last_page_is_pulled_back_with_slightly_smaller_references(tmp_path):
    before, inputs, after = _end_before_after(tmp_path, 1500, 120, _pull_back_short_tail)
    assert (before["page"], before["col"]) == (4, 0) and before["used"] < 0.2 * before["avail"]
    assert float(inputs[REF_SCALE_INPUT]) >= 0.9 and after["page"] == 3


def test_full_compile_balances_and_stays_warning_free(tmp_path):
    body = "<sec><title>S</title><p>" + " ".join(["filler"] * 1500) + "</p></sec>"
    *_, warnings = build(tmp_path, body, back=_refs(60))
    assert warnings == [] and sorted(p.name for p in tmp_path.glob("*.pdf")) == ["doc.pdf"]


def test_heading_of_a_section_of_only_floats_goes_with_its_first_float(tmp_path):
    # 2025.06.26.661757 "Appendix D: Supplementary tables": the tables went to the tops of
    # pages, the heading stayed in the text, alone at the end of the article.
    body = (
        "<sec><title>Results</title><p>" + " ".join(["filler"] * 300) + "</p></sec>"
        '<sec id="supp"><title>Supplementary figures</title>' + fig("s1", "Figure S1") + fig("s2", "Figure S2") + "</sec>"
    )
    _, source, _, warnings = build(tmp_path, body, sizes={"s1": (800, 600), "s2": (800, 600)})
    assert "header: [= Supplementary figures <sec-supp>]" in source.split("lbl: [Figure S1]")[1].split("\n")[0]
    assert "\n= Supplementary figures" not in source and "header:" not in source.split("lbl: [Figure S2]")[1].split("\n")[0]
    assert warnings == []
    raw = typst.query(str(tmp_path / "doc.typ"), "heading", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True)
    titles = [h["body"]["text"] for h in json.loads(raw) if h["body"].get("text")]
    assert titles.count("Supplementary figures") == 1  # still a heading (bookmark, link target)


def test_floats_after_all_the_text_get_pages_of_their_own(tmp_path):
    # 2025.11.20.689526: supplementary figures after the references went to the tops of the last
    # text pages and pushed the end of the reference list under them.
    body = "<sec><title>S</title><p>" + " ".join(["filler"] * 400) + "</p></sec>"
    back = (REFS + '<sec id="sf"><title>Supplementary figures</title>' + fig("s1", "Figure S1") + "</sec>"
            + fig("s2", "Figure S2"))
    _, source, _, warnings = build(tmp_path, body, back=back, sizes={"s1": (800, 600), "s2": (800, 600)})
    assert "lbl: [Figure S1]" in tail(source) and "lbl: [Figure S2]" in tail(source)
    assert "#references-list" not in tail(source) and warnings == []
    # a figure section followed by text is not "after all the text"
    back = REFS + '<sec><title>Supplementary figures</title>' + fig("s1", "Figure S1") + "</sec><sec><title>Movies</title><p>Movie 1.</p></sec>"
    _, source, _, _ = build(tmp_path, body, back=back, sizes={"s1": (800, 600)})
    assert "\n  tail: none," in source


def test_trailing_figure_subsection_of_the_last_section_goes_to_the_tail(tmp_path):
    # 2025.08.28.672890: "S2 Supplement" > "S2.1 ..." (text) > "S2.2 Figures" (only figures).
    body = "<sec><title>S</title><p>" + " ".join(["filler"] * 300) + "</p></sec>"
    back = (REFS + '<sec><title>S2 Supplement</title><sec><title>S2.1 Model</title><p>Text.</p></sec>'
            '<sec id="figs"><title>S2.2 Figures</title>' + fig("s1", "Figure S1") + "</sec></sec>")
    _, source, _, warnings = build(tmp_path, body, back=back, sizes={"s1": (800, 600)})
    assert "header: [== S2\\.2 Figures <sec-figs>]" in tail(source) and "S2\\.1 Model" not in tail(source)
    assert source.count("S2\\.2 Figures") == 1 and warnings == []


def test_nested_float_only_sections_pass_their_headings_down(tmp_path):
    # 2025.02.23.639383: "SUPPLEMENTARY INFORMATION" > "Supplementary Figures" / "Supplementary Tables".
    body = "<sec><title>S</title><p>" + " ".join(["filler"] * 300) + "</p></sec>"
    back = (REFS + '<sec id="si"><title>Supplementary information</title>'
            '<sec id="sf"><title>Supplementary figures</title>' + fig("s1", "Figure S1") + fig("s2", "Figure S2") + "</sec>"
            '<sec id="st"><title>Supplementary tables</title>' + fig("s3", "Figure S3") + "</sec></sec>")
    _, source, _, warnings = build(tmp_path, body, back=back, sizes={k: (800, 600) for k in ("s1", "s2", "s3")})
    headers = [f.split("header: [", 1)[1].split("])", 1)[0] for f in tail(source).split("#figure-block(")[1:] if "header: [" in f]
    assert headers == ["= Supplementary information <sec-si>\n== Supplementary figures <sec-sf>", "== Supplementary tables <sec-st>"]
    assert warnings == []
    raw = typst.query(str(tmp_path / "doc.typ"), "heading", root=str(tmp_path), font_paths=font_dirs(), ignore_system_fonts=True)
    levels = {h["body"].get("text"): h["level"] for h in json.loads(raw)}
    assert levels["Supplementary information"] == 1 and levels["Supplementary figures"] == 2
