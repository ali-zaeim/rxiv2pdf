"""Native-JATS articles from other journals (PLOS, BMC via Europe PMC): the XML-table and
MathML paths that bioRxiv never exercises (it ships tables and equations as images).
See tests/fixtures/external/README.md for sources and licenses (all CC BY).
"""

from pathlib import Path

import pytest
from lxml import etree
from test_render import compile_article

from rxiv2pdf.jats import parse_jats
from rxiv2pdf.mathml import mathml_to_segments, mathml_to_typst, validate
from rxiv2pdf.model import Section, TableBlock
from rxiv2pdf.render_typst import render

EXTERNAL = sorted((Path(__file__).parent / "fixtures" / "external").glob("*.xml"))
MATHML_NS = "{http://www.w3.org/1998/Math/MathML}math"


def _walk(blocks):
    for b in blocks:
        yield b
        if isinstance(b, Section):
            yield from _walk(b.blocks)


@pytest.mark.parametrize("xml", EXTERNAL, ids=lambda p: p.stem)
def test_parses_and_compiles_cleanly(xml, tmp_path):
    article = parse_jats(xml.read_bytes())
    assert article.warnings == []
    source, render_warnings, typst_warnings = compile_article(article, tmp_path)
    assert render_warnings == [] and typst_warnings == []  # incl. no MathML fallbacks
    assert "&author=" not in source and "publication_year=" not in source  # Europe PMC machine links dropped


WITH_MATH = [p for p in EXTERNAL if next(etree.parse(str(p)).getroot().iter(MATHML_NS), None) is not None]


@pytest.mark.parametrize("xml", WITH_MATH, ids=lambda p: p.stem)
def test_every_mathml_formula_converts_and_typesets(xml):
    root = etree.parse(str(xml)).getroot()
    formulas = [etree.tostring(m, encoding="unicode") for m in root.iter(MATHML_NS)]
    converted = [mathml_to_typst(f) for f in formulas]
    assert formulas and all(converted)
    assert all(validate(converted))
    for f, c in zip(formulas, converted, strict=True):
        segs = mathml_to_segments(f)
        assert segs is None or " ".join(segs) == c


@pytest.mark.parametrize(
    ("pmcid", "tables", "spans"),
    [("PMC12459853", 4, 57), ("PMC11706453", 3, 32), ("PMC12625201", 3, 29), ("PMC12742746", 4, 23)],
)
def test_xml_tables_parse_with_spans(pmcid, tables, spans):
    article = parse_jats(next(p for p in EXTERNAL if p.stem == pmcid).read_bytes())
    found = [b.table for b in _walk(article.body + article.back) if isinstance(b, TableBlock) and b.table]
    assert len(found) == tables and not any(t.malformed for t in found)
    cells = [c for t in found for row in t.head + t.body + t.foot for c in row]
    assert sum(c.colspan > 1 or c.rowspan > 1 for c in cells) == spans


def test_source_section_numbers_are_not_doubled():
    article = parse_jats(next(p for p in EXTERNAL if p.stem == "PMC12204626").read_bytes())
    assert article.source_numbered  # titles read "2.2. Partial differential equation model"
    source, _ = render(article, assets={})
    assert "section-numbering: false" in source


def test_europe_pmc_reference_list_in_body_is_not_duplicated():
    article = parse_jats(next(p for p in EXTERNAL if p.stem == "PMC12204626").read_bytes())
    source, _ = render(article, assets={})
    assert source.count("#ref-entry(") == len(article.references) == 49
