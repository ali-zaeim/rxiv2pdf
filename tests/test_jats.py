"""JATS -> Article. Snapshots for every real fixture plus targeted checks.

Regenerate snapshots after an intended model change with:  UPDATE_SNAPSHOTS=1 uv run pytest tests/test_jats.py
"""

import json
import os
from pathlib import Path

import pytest

from rxiv2pdf.jats import parse_jats
from rxiv2pdf.model import (
    Cite,
    DisplayMath,
    Figure,
    InlineMath,
    Link,
    ListBlock,
    Paragraph,
    ReferenceListMarker,
    Section,
    Styled,
    Supplement,
    TableBlock,
    Text,
    XRef,
    plain_text,
    to_data,
)

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOTS = Path(__file__).parent / "snapshots"
XML_FILES = sorted(FIXTURES.glob("*/v*.source.xml"))


def load(name: str):
    path = next(FIXTURES.glob(f"{name}/v*.source.xml"))
    return parse_jats(path.read_bytes())


def walk(blocks):
    for b in blocks:
        yield b
        if isinstance(b, Section):
            yield from walk(b.blocks)


def paragraphs(blocks):
    return [b for b in walk(blocks) if isinstance(b, Paragraph)]


def cites(article):
    return [n for p in paragraphs(article.body) for n in p.content if isinstance(n, Cite)]


# --- snapshots ---------------------------------------------------------------------------
@pytest.mark.parametrize("xml", XML_FILES, ids=lambda p: p.parent.name)
def test_snapshot(xml):
    article = parse_jats(xml.read_bytes())
    data = json.dumps(to_data(article), indent=1, ensure_ascii=False, sort_keys=True)
    snap = SNAPSHOTS / f"{xml.parent.name}.json"
    if os.environ.get("UPDATE_SNAPSHOTS") or not snap.exists():
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"Missing snapshot {snap.name}; run with UPDATE_SNAPSHOTS=1 to create it")
        snap.parent.mkdir(exist_ok=True)
        snap.write_text(data + "\n", encoding="utf-8")
    assert data + "\n" == snap.read_text(encoding="utf-8")


@pytest.mark.parametrize("xml", XML_FILES, ids=lambda p: p.parent.name)
def test_real_fixtures_parse_without_warnings(xml):
    article = parse_jats(xml.read_bytes())
    assert article.warnings == []
    assert article.title and article.authors and article.references and article.body
    expected = "author-year" if xml.parent.name in ("2026.09.25.754430", "2026.10.06.756929") else "numeric"
    assert article.cite_mode == expected


# --- front matter --------------------------------------------------------------------------
def test_front_matter_590000():
    a = load("2024.04.17.590000")
    assert plain_text(a.title) == "Molecular basis of proton-sensing by G protein-coupled receptors"
    assert a.doi == "10.1101/2024.04.17.590000"
    first = a.authors[0]
    assert (first.given, first.surname, first.orcid) == ("Matthew K.", "Howard", "0000-0002-2128-2065")
    assert first.aff_ids == ["a1", "a2", "a3"] and first.equal_contrib and not first.corresponding
    assert [x.display_name for x in a.authors if x.corresponding] == ["Willow Coyote-Maestas", "Aashish Manglik"]
    # Affiliations ordered by first appearance; label stripped, internal punctuation kept.
    assert plain_text(a.affiliations[0].content) == "Tetrad graduate program, University of California, San Francisco, CA, USA"
    assert plain_text(a.equal_contrib_note) == "These authors contributed equally to this work"
    corresp = a.corresp[0]
    assert plain_text(corresp).startswith("Corresponding author. Email: willow.coyote-maestas@ucsf.edu (W.C.M.)")
    assert any(isinstance(n, Link) and n.url == "mailto:aashish.manglik@ucsf.edu" for n in corresp)
    assert a.license_url == "http://creativecommons.org/licenses/by-nc-nd/4.0/"


def test_affiliations_ordered_by_first_author_reference():
    a = load("2020.03.22.002386")  # 100 authors, 30 affiliations
    seen = []
    for au in a.authors:
        for rid in au.aff_ids:
            if rid not in seen:
                seen.append(rid)
    assert [x.id for x in a.affiliations][: len(seen)] == seen


def test_structured_abstract_and_keywords():
    a = load("2026.10.16.759679")
    assert [plain_text(s.title) for s in a.abstract] == ["Background", "Methods", "Results", "Conclusion"]
    assert all(isinstance(s, Section) for s in a.abstract)
    assert a.keywords[:2] == ["Alzheimer’s disease", "β-amyloid"]


# --- citations ----------------------------------------------------------------------------
def test_sup_citation_range_with_en_dash_expands():
    # <sup><xref>1</xref>–<xref>5</xref></sup>, <sup><xref>6</xref>,<xref>7</xref></sup>
    c = cites(load("2024.04.17.590000"))
    assert c[0].ref_ids == ["c1", "c2", "c3", "c4", "c5"]
    assert c[1].ref_ids == ["c6", "c7"]


def test_hyphen_range_expands():
    # <sup><xref>1</xref>-<xref>3</xref></sup> with a plain hyphen (seen in bioRxiv papers).
    a = parse_jats(jats(body='<p>Text<sup><xref ref-type="bibr" rid="r1">1</xref>-<xref ref-type="bibr" rid="r3">3</xref></sup>.</p>', back=REFS))
    assert cites(a)[0].ref_ids == ["r1", "r2", "r3"]


def test_bracketed_citations_lose_brackets_and_preceding_space():
    # "... disease trajectory [<xref>1</xref>–<xref>4</xref>]. Recent ..."
    a = load("2026.10.16.759679")
    p = next(p for p in paragraphs(a.body) if any(isinstance(n, Cite) for n in p.content))
    i = next(i for i, n in enumerate(p.content) if isinstance(n, Cite))
    assert p.content[i].ref_ids == ["c1", "c2", "c3", "c4"]
    assert p.content[i - 1].text.endswith("disease trajectory")
    assert p.content[i + 1].text.startswith(". Recent")


def test_adjacent_bracketed_groups_become_one_citation():
    # "[1], [2]; [4]" would otherwise print as two superscripts with a baseline comma between.
    a = parse_jats(jats(body=(
        '<sec><title>S</title><p>Prior work [<xref ref-type="bibr" rid="r1">1</xref>], [<xref ref-type="bibr" rid="r2">2</xref>]; '
        '[<xref ref-type="bibr" rid="r4">4</xref>]. Next [<xref ref-type="bibr" rid="r3">3</xref>] and [<xref ref-type="bibr" rid="r1">1</xref>].</p></sec>'
    ), back=REFS))
    assert [c.ref_ids for c in cites(a)] == [["r1", "r2", "r4"], ["r3"], ["r1"]]
    assert [n.text for n in paragraphs(a.body)[0].content if isinstance(n, Text)] == ["Prior work", ". Next", " and", "."]


def test_citation_inside_italic_inside_sup_is_unwrapped():
    # <sup><italic><xref rid="c62">62</xref></italic></sup>
    a = load("2020.03.22.002386")
    found = [n for p in paragraphs(a.body) for n in p.content if isinstance(n, Cite) and n.ref_ids == ["c62"]]
    assert found
    styled_cites = [
        n for p in paragraphs(a.body) for n in p.content
        if isinstance(n, Styled) and any(isinstance(k, Cite) for k in n.children)
    ]
    assert styled_cites == []


def test_figure_xrefs_keep_text_and_target():
    a = load("2026.10.22.760766")
    xrefs = [n for p in paragraphs(a.body) for n in p.content if isinstance(n, XRef) and n.kind == "fig"]
    assert xrefs[0].target == "fig1" and plain_text(xrefs[0].children) == "Fig. 1A"


# --- references ---------------------------------------------------------------------------
def test_citation_element_kept_as_provided_text():
    a = load("2024.04.17.590000")
    r = a.references[0]
    assert (r.id, r.number, r.label, r.kind, r.citation_type) == ("c1", 1, "1.", "mixed", "journal")
    assert plain_text(r.content) == (
        "Jasti, J., Furukawa, H., Gonzales, E. B. & Gouaux, E. Structure of acid-sensing ion channel 1 "
        "at 1.9 A resolution and low pH. Nature 449, 316–323 (2007)."
    )
    assert len(a.references) == 77 and a.references[-1].number == 77


def test_reference_doi_becomes_link():
    r = load("2025.11.24.687809").references[0]
    assert r.doi == "10.1016/j.neuroscience.2017.07.061"
    assert any(isinstance(n, Link) and n.url == "https://doi.org/10.1016/j.neuroscience.2017.07.061" for n in r.content)


# --- body / back structure ----------------------------------------------------------------
def test_figures_tables_and_supplements_old_paper():
    a = load("2020.03.22.002386")
    figs = [b for b in walk(a.body) if isinstance(b, Figure)]
    tables = [b for b in walk(a.body) if isinstance(b, TableBlock)]
    assert [f.label for f in figs] == ["Figure 1", "Figure 2", "Figure 3", "Figure 4", "Figure 5"]
    assert plain_text(figs[0].title).startswith("AP-MS Workflow for Identification")
    assert figs[0].graphics == ["002386v3_fig1"] and figs[0].id == "fig1"
    assert tables[0].label == "Table 1a" and len(tables[0].graphics) == 5 and not tables[0].has_xml_table
    supps = [b for b in walk(a.body) if isinstance(b, Supplement)]
    assert supps[0].label == "Extended Data Figures" and supps[0].href == "supplements/002386_file07.pdf"


def test_display_formulas_and_extended_data_in_back():
    a = load("2026.10.22.760766")
    maths = [b for b in walk(a.body) if isinstance(b, DisplayMath)]
    assert [m.image for m in maths] == ["760766v1_ueqn1.gif", "760766v1_ueqn2.gif"]
    # The formula sat inside a <p>: text before/after became separate paragraphs.
    methods = [b for b in walk(a.body) if isinstance(b, Section)]
    owner = next(s for s in methods if any(isinstance(b, DisplayMath) for b in s.blocks))
    i = next(i for i, b in enumerate(owner.blocks) if isinstance(b, DisplayMath))
    assert plain_text(owner.blocks[i - 1].content).endswith("was defined as")
    assert owner.blocks[i + 1].continues
    # Back matter keeps source order: ... references, then Extended data figures.
    kinds = [type(b).__name__ for b in a.back]
    assert kinds.index("ReferenceListMarker") < len(kinds) - 1
    ext = [b for b in walk(a.back) if isinstance(b, Figure)]
    assert [f.label for f in ext] == [f"Extended data Fig. {i}" for i in range(1, 5)]
    assert all(f.supplementary for f in ext)
    assert not any(f.supplementary for f in walk(a.body) if isinstance(f, Figure))
    # Nature-style "| " separator stripped from caption titles.
    assert plain_text(ext[0].title).startswith("Design, screening")


def test_back_matter_sections_and_glossary():
    a = load("2020.03.22.002386")
    titles = [plain_text(b.title) for b in a.back if isinstance(b, Section)]
    assert titles[0] == "Abbreviations" and "ACKNOWLEDGEMENTS" in titles
    assert isinstance(a.back[-1], ReferenceListMarker) and plain_text(a.back[-1].title) == "REFERENCES"


# --- synthetic documents for cases no fixture covers ----------------------------------------
def jats(body: str = "", back: str = "", front_extra: str = "") -> bytes:
    return f"""<article xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:mml="http://www.w3.org/1998/Math/MathML">
<front><article-meta><article-id pub-id-type="doi">10.1101/2020.01.01.000001</article-id>
<title-group><article-title>T</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Doe</surname><given-names>Jane</given-names></name></contrib>
<contrib contrib-type="author"><collab>The Big Consortium</collab></contrib></contrib-group>{front_extra}
</article-meta></front><body>{body}</body><back>{back}</back></article>""".encode()


REFS = """<ref-list><title>References</title>
<ref id="r1"><label>1</label><mixed-citation>One.</mixed-citation></ref>
<ref id="r2"><label>2</label><mixed-citation>Two.</mixed-citation></ref>
<ref id="r3"><label>3</label><mixed-citation>Three.</mixed-citation></ref>
<ref id="r4"><label>4</label><mixed-citation>Four.</mixed-citation></ref></ref-list>"""


def test_unknown_elements_keep_text_and_warn_once(caplog):
    a = parse_jats(jats(
        body='<sec><title>S</title><p>Hello <frobnicate>wonky <b>bold</b></frobnicate> world <frobnicate>again</frobnicate>.</p>'
             '<mystery-block><p>Block text</p></mystery-block></sec>',
    ))
    sec = a.body[0]
    assert plain_text(sec.blocks[0].content) == "Hello wonky bold world again."
    assert plain_text(sec.blocks[1].content) == "Block text"
    assert a.warnings == [
        "Unknown JATS element <frobnicate> in text: rendered as plain text",
        "Unknown JATS element <mystery-block> in body: rendered as plain text",
    ]


def test_collab_author():
    a = parse_jats(jats())
    assert a.authors[1].collab == "The Big Consortium" and a.authors[1].display_name == "The Big Consortium"


def test_other_author_notes_kept_with_their_own_marks():
    notes = """<author-notes><fn id="n1" fn-type="equal"><label>*</label><p>equal contribution</p></fn>
        <fn id="n2" fn-type="others"><label>13</label><p>Lead contact.</p></fn>
        <fn id="n3" fn-type="present-address"><label>#a</label><p>Now at Univ. X</p></fn>
        <fn id="n4" fn-type="others"><label>**</label><p>lead contact</p></fn>
        <fn fn-type="others"><p>These authors jointly supervised this work</p></fn>
        <fn id="n5" fn-type="conflict"><p>Conflict of interest: none.</p></fn></author-notes>"""
    xml = jats(front_extra=notes).replace(
        b"<given-names>Jane</given-names></name>",
        b'<given-names>Jane</given-names></name><xref ref-type="fn" rid="n3">#a</xref><xref ref-type="fn" rid="n2">13</xref>',
    )
    a = parse_jats(xml)
    # numeric labels would clash with the renumbered affiliations: own marks, in note order;
    # "**" is linked to no author (its mark would point nowhere) and conflict notes go to the back
    assert [(m, plain_text(c)) for m, c in a.author_notes] == [
        ("‡", "Lead contact."), ("§", "Now at Univ. X"), ("", "These authors jointly supervised this work"),
    ]
    assert a.authors[0].note_marks == ["§", "‡"] and a.authors[1].note_marks == []


def test_mixed_separators_ranges_and_unknown_rid():
    a = parse_jats(jats(
        body='<p>A (<xref ref-type="bibr" rid="r1">1</xref>-<xref ref-type="bibr" rid="r3">3</xref>) and '
             'B<sup><xref ref-type="bibr" rid="r4">4</xref>,<xref ref-type="bibr" rid="r9">9</xref></sup>.</p>',
        back=REFS,
    ))
    content = a.body[0].content
    assert content[0] == Text("A")
    assert content[1] == Cite(["r1", "r2", "r3"])
    assert content[2] == Text(" and B")
    assert content[3] == Cite(["r4", "r9"])
    assert a.warnings == ["Citation points to unknown reference id 'r9'"]


def test_author_year_citations_keep_their_text():
    a = parse_jats(jats(
        body='<p>As shown (<xref ref-type="bibr" rid="r1">Smith et al., 2020</xref>; '
             '<xref ref-type="bibr" rid="r2">Jones, 2019</xref>).</p>',
        back=REFS,
    ))
    assert a.cite_mode == "author-year"
    xrefs = [n for n in a.body[0].content if isinstance(n, XRef)]
    assert [(x.target, plain_text(x.children)) for x in xrefs] == [("r1", "Smith et al., 2020"), ("r2", "Jones, 2019")]
    assert plain_text(a.body[0].content) == "As shown (Smith et al., 2020; Jones, 2019)."


def test_element_citation_is_formatted():
    a = parse_jats(jats(back="""<ref-list><ref id="r1"><element-citation publication-type="journal">
        <person-group person-group-type="author"><name><surname>Smith</surname><given-names>John A</given-names></name>
        <name><surname>Lee</surname><given-names>K.</given-names></name><etal/></person-group>
        <article-title>A result</article-title><source>Nature</source><year>2020</year><volume>5</volume>
        <issue>2</issue><fpage>10</fpage><lpage>20</lpage><pub-id pub-id-type="doi">10.1/x</pub-id>
        </element-citation></ref></ref-list>"""))
    r = a.references[0]
    assert r.kind == "element" and r.doi == "10.1/x"
    assert plain_text(r.content) == "Smith JA, Lee K, et al. A result. Nature. 2020;5(2):10–20. doi:10.1/x"
    assert any(isinstance(n, Styled) and n.style == "italic" for n in r.content)


def test_lists_and_inline_tex_math():
    a = parse_jats(jats(body=(
        '<p>Steps:<list list-type="order"><list-item><p>first</p></list-item><list-item><p>second</p></list-item></list>'
        'Then <inline-formula><tex-math>\\alpha+1</tex-math></inline-formula>.</p>'
    )))
    blocks = a.body
    assert plain_text(blocks[0].content) == "Steps:"
    assert isinstance(blocks[1], ListBlock) and blocks[1].ordered and len(blocks[1].items) == 2
    assert blocks[2].continues
    math = [n for n in blocks[2].content if isinstance(n, InlineMath)]
    assert math[0].tex == "\\alpha+1"


# --- superscripts that bioRxiv's XML wrongly tags as citations -----------------------------
def _sup_text(nodes):
    return "".join(plain_text(n.children) for n in nodes if isinstance(n, Styled) and n.style == "sup")


@pytest.mark.parametrize(
    ("snippet", "visible"),
    [
        # GPCR residue numbering split across two sups (as in the 2024.04.17.590000 fixture)
        ('D118<sup><xref ref-type="bibr" rid="r3">3</xref></sup><sup>.<xref ref-type="bibr" rid="r4">4</xref>9</sup> motif', "D1183.49 motif"),
        # ... or inside one sup
        ('C240<sup><xref ref-type="bibr" rid="r2">2</xref>.47</sup> and', "C2402.47 and"),
        # powers of ten after ×10 / x 10
        ('1.2×10<sup><xref ref-type="bibr" rid="r4">4</xref></sup> cells', "1.2×104 cells"),
        ('3.0 x 10<sup><xref ref-type="bibr" rid="r3">3</xref></sup> cells', "3.0 x 103 cells"),
    ],
)
def test_false_citations_are_unlinked(snippet, visible):
    a = parse_jats(jats(body=f"<p>{snippet}</p>", back=REFS))
    content = a.body[0].content
    assert not any(isinstance(n, Cite) for n in content)
    assert plain_text(content) == visible
    assert a.corrections and "tagged as citations" in a.corrections[0]


@pytest.mark.parametrize(
    "snippet",
    [
        'GPR68<sup><xref ref-type="bibr" rid="r1">1</xref>,<xref ref-type="bibr" rid="r2">2</xref></sup>.',
        'late 2019<sup><xref ref-type="bibr" rid="r3">3</xref></sup>, is',
        'IL-10<sup><xref ref-type="bibr" rid="r4">4</xref></sup> levels',  # no "×": a real citation
        'SerialEM 3.8<sup><xref ref-type="bibr" rid="r2">2</xref></sup>.',  # decimal BEFORE the sup is fine
        'channels<sup><xref ref-type="bibr" rid="r1">1</xref>–<xref ref-type="bibr" rid="r4">4</xref></sup>.',
        # Known limitation: a peptide range looks exactly like a citation range and stays linked.
        'NTS<sup><xref ref-type="bibr" rid="r1">1</xref>–<xref ref-type="bibr" rid="r3">3</xref></sup> peptide',
    ],
)
def test_real_citations_are_left_alone(snippet):
    a = parse_jats(jats(body=f"<p>{snippet}</p>", back=REFS))
    assert any(isinstance(n, Cite) for n in a.body[0].content)
    assert a.corrections == []


def test_residue_numbers_restored_in_real_fixture():
    a = load("2024.04.17.590000")
    assert a.corrections == [
        "Unlinked 57 decimal superscripts that the source XML tagged as citations (e.g. “P^5.50)",
        "Unlinked 1 power of ten that the source XML tagged as citations (e.g. 10^6)",
    ]
    p = next(p for p in paragraphs(a.body) if "D118" in plain_text(p.content))
    i = next(i for i, n in enumerate(p.content) if isinstance(n, Text) and n.text.endswith("D118"))
    assert _sup_text(p.content[i + 1 : i + 3]) == "3.49"


def test_power_of_ten_with_asterisk_operator():
    # "injecting 2∗10<sup>6</sup> cells" (random-sample paper 2026.10.02.756321)
    a = parse_jats(jats(body='<p>injecting 2∗10<sup><xref ref-type="bibr" rid="r4">4</xref></sup> cells</p>', back=REFS))
    assert not any(isinstance(n, Cite) for n in a.body[0].content)
    assert plain_text(a.body[0].content) == "injecting 2∗104 cells"


# --- back matter kept only in <front> --------------------------------------------------------
def _back_titles(a):
    return [("REFS" if isinstance(b, ReferenceListMarker) else plain_text(b.title)) for b in a.back
            if isinstance(b, Section | ReferenceListMarker)]


def test_structured_funding_added_when_back_has_none():
    a = load("2025.11.24.687809")
    titles = _back_titles(a)
    assert titles[-3:] == ["Funding", "Summary of updates", "REFS"]  # added just before the references
    funding = next(b for b in a.back if isinstance(b, Section) and b.kind == "funding")
    assert plain_text(funding.blocks[0].content) == "European Research Council (101001270)."


def test_existing_back_sections_are_not_duplicated():
    a = load("2024.04.17.590000")  # has its own Funding and Competing Interests sections
    assert not any(isinstance(b, Section) and b.kind in ("funding", "competing-interests") for b in a.back)
    a = load("2026.10.16.759679")  # "Competing interests" nested inside "Declarations"
    assert not any(isinstance(b, Section) and b.kind == "competing-interests" for b in a.back)


def test_medrxiv_competing_interests_and_declarations_from_front():
    a = load("2026.10.02.26364566")
    titles = _back_titles(a)
    assert titles[titles.index("REFS") - 2 : titles.index("REFS")] == ["Competing interests", "Author Declarations"]


def test_summary_of_updates_and_unrepeated_external_links_go_to_back_matter():
    front = """<notes><fn-group content-type="summary-of-updates"><title>Summary of Updates:</title>
      <fn fn-type="update"><p>Added a figure.</p></fn></fn-group>
      <fn-group content-type="external-links">
      <fn fn-type="dataset"><p><ext-link ext-link-type="uri" xlink:href="https://doi.org/10.5281/zenodo.111">https://doi.org/10.5281/zenodo.111</ext-link></p></fn>
      <fn fn-type="dataset"><p><ext-link ext-link-type="uri" xlink:href="https://doi.org/10.5281/zenodo.222">https://doi.org/10.5281/zenodo.222</ext-link></p></fn>
      </fn-group></notes>"""
    a = parse_jats(jats(body="<sec><title>Data</title><p>Code: 10.5281/zenodo.222.</p></sec>", back=REFS, front_extra=front))
    titles = _back_titles(a)
    assert titles[titles.index("REFS") - 2 : titles.index("REFS")] == ["Summary of updates", "External links"]
    links = next(b for b in a.back if isinstance(b, Section) and b.kind == "external-links")
    assert [item[0].content[0].url for item in links.blocks[0].items] == ["https://doi.org/10.5281/zenodo.111"]


def test_list_numbering_style_is_kept():
    a = parse_jats(jats(body=(
        '<sec><title>S</title><list list-type="alpha-lower"><list-item><p>x</p></list-item></list>'
        '<list list-type="roman-upper"><list-item><p>y</p></list-item></list>'
        '<list list-type="order"><list-item><p>z</p></list-item></list></sec>'
    )))
    lists = [b for b in walk(a.body) if isinstance(b, ListBlock)]
    assert [(x.ordered, x.style) for x in lists] == [(True, "alpha-lower"), (True, "roman-upper"), (True, None)]


def test_caption_without_title_loses_its_leading_separator():
    a = parse_jats(jats(body=(
        '<sec><title>S</title><fig id="f1"><label>Figure 1</label><caption><p>– Timeline and stages</p>'
        '<p>Body.</p></caption><graphic xlink:href="f1"/></fig></sec>'
    )))
    f = next(b for b in walk(a.body) if isinstance(b, Figure))
    assert plain_text(f.caption[0].content) == "Timeline and stages"


def test_funding_statement_preferred_and_ror_ids_dropped():
    a = parse_jats(jats(front_extra="""<funding-group><award-group><funding-source>Wellcome Trust
        <institution-wrap><institution-id>https://ror.org/029chgv08</institution-id></institution-wrap></funding-source>
        <award-id>221234/Z</award-id></award-group></funding-group>""", back=REFS))
    funding = next(b for b in a.back if isinstance(b, Section) and b.kind == "funding")
    assert plain_text(funding.blocks[0].content) == "Wellcome Trust (221234/Z)."
    a = parse_jats(jats(front_extra="<funding-group><funding-statement>Funded by the MRC.</funding-statement></funding-group>", back=REFS))
    funding = next(b for b in a.back if isinstance(b, Section) and b.kind == "funding")
    assert plain_text(funding.blocks[0].content) == "Funded by the MRC."


def test_medrxiv_funding_statement_kept_unless_said_elsewhere():
    note = ('<notes notes-type="financial-disclosure"><title>Funding Statement</title>'
            "<p>This study did not receive any funding</p></notes>")
    a = parse_jats(jats(front_extra=note, back=REFS))
    funding = [b for b in a.back if isinstance(b, Section) and b.kind == "funding"]
    assert [plain_text(f.title) for f in funding] == ["Funding"]
    assert plain_text(funding[0].blocks[0].content) == "This study did not receive any funding"
    # the authors repeat it (here in the acknowledgements): not added twice
    ack = "<ack><title>Acknowledgements</title><p>We thank X. This study did not receive any funding.</p></ack>"
    a = parse_jats(jats(front_extra=note, back=ack + REFS))
    assert not any(isinstance(b, Section) and b.kind == "funding" for b in a.back)
    # the authors have their own funding section
    own = "<sec><title>Funding</title><p>None.</p></sec>"
    a = parse_jats(jats(front_extra=note, back=own + REFS))
    assert [plain_text(b.title) for b in a.back if isinstance(b, Section)].count("Funding") == 1


def test_doubly_escaped_character_references_are_decoded():
    # 2025.03.06.641668, a reference title: "Interferon &amp;#x3b3;" printed as "Interferon &#x3b3;".
    # 2025.11.13.687960: "Genomics, Proteomics &amp;amp; Bioinformatics" printed "&amp;".
    a = parse_jats(jats(body="<p>Interferon &amp;#x3b3; and &amp;#947;; &amp;#0; stays; R &amp;amp; D.</p>"))
    assert plain_text(a.body[0].content) == "Interferon γ and γ; &#0; stays; R & D."
    assert a.corrections == ['Decoded 3 doubly escaped character references in the source XML (e.g. "&#x3b3;" -> "γ")']


def test_two_equal_contribution_groups_get_their_own_marks():
    # 2025.08.24.671960: co-first and co-senior authors, both equal-contrib="yes".
    notes = """<author-notes><fn id="n1" fn-type="equal"><label>†</label><p>Co-first authors.</p></fn>
        <fn id="n2" fn-type="equal"><label>#</label><p>Co-senior authors</p></fn></author-notes>"""
    xml = jats(front_extra=notes).replace(
        b'<contrib contrib-type="author"><name><surname>Doe</surname><given-names>Jane</given-names></name>',
        b'<contrib contrib-type="author" equal-contrib="yes"><name><surname>Doe</surname><given-names>Jane</given-names></name>'
        b'<xref ref-type="fn" rid="n1">\xe2\x80\xa0</xref>',
    ).replace(
        b'<contrib contrib-type="author"><collab>The Big Consortium</collab>',
        b'<contrib contrib-type="author" equal-contrib="yes"><collab>The Big Consortium</collab><xref ref-type="fn" rid="n2">#</xref>',
    )
    a = parse_jats(xml)
    assert plain_text(a.equal_contrib_note) == "Co-first authors."
    assert [au.equal_contrib for au in a.authors] == [True, False]
    assert [(m, plain_text(c)) for m, c in a.author_notes] == [("‡", "Co-senior authors")]
    assert a.authors[1].note_marks == ["‡"]


def test_funding_footnote_among_author_notes_goes_to_back_matter():
    # 2025.08.24.671960: <fn fn-type="supported-by"><p><bold>Funding</bold>: This study ...
    notes = ('<author-notes><fn fn-type="supported-by"><p><bold>Funding</bold>: This study received funding '
             "from the ERC (951294).</p></fn></author-notes>")
    a = parse_jats(jats(front_extra=notes, back=REFS))
    funding = [b for b in a.back if isinstance(b, Section) and b.kind == "funding"]
    assert [plain_text(f.blocks[0].content) for f in funding] == ["This study received funding from the ERC (951294)."]
    assert a.author_notes == []


def test_reference_with_several_works_keeps_all_of_them():
    # 2025.08.24.671960: "[2] a) ...; b) ..." - one <ref>, a <citation> with a <label> per work.
    back = """<ref-list><title>References</title><ref id="c2"><label>[2]</label>
        <citation id="c2a"><label>a)</label>Trejo-Lopez JA, <source>Neurotherapeutics</source> <year>2023</year>;</citation>
        <citation id="c2b"><label>b)</label>Thal DR, <source>Sci Aging Knowl Environ</source> <year>2006</year>.</citation>
        </ref></ref-list>"""
    a = parse_jats(jats(back=back))
    assert plain_text(a.references[0].content) == "a) Trejo-Lopez JA, Neurotherapeutics 2023; b) Thal DR, Sci Aging Knowl Environ 2006."


def test_authors_conflict_note_wins_over_the_submission_form_statement():
    # 2025.11.13.687960: the form's "The authors have declared no competing interest." replaced
    # the author's own "Conflict of Interest: The author declares no conflict of interest".
    front = ('<author-notes><fn fn-type="conflict"><p>Conflict of Interest: The author declares no conflict of interest</p></fn>'
             '</author-notes><notes notes-type="competing-interest-statement"><title>Competing Interest Statement</title>'
             "<p>The authors have declared no competing interest.</p></notes>")
    a = parse_jats(jats(front_extra=front, back=REFS))
    ci = [b for b in a.back if isinstance(b, Section) and b.kind == "competing-interests"]
    assert [plain_text(c.blocks[0].content) for c in ci] == ["The author declares no conflict of interest"]  # lead-in dropped


def test_code_glued_to_the_next_word_gets_its_space_back():
    # 2025.06.16.660047: "<monospace>GT</monospace>dinucleotide", "in <monospace>G</monospace>and".
    a = parse_jats(jats(body="<p>A <monospace>GT</monospace>dinucleotide, in <monospace>G</monospace>and "
                             "the <monospace>ID</monospace>s, <monospace>AG</monospace>. Done.</p>"))
    assert plain_text(a.body[0].content) == "A GT dinucleotide, in G and the IDs, AG. Done."
    assert a.corrections == ['Inserted 2 missing spaces after code-formatted text (e.g. "GTdinucleotide")']
