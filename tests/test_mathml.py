"""MathML -> Typst math. No real bioRxiv paper sampled had MathML; these are synthetic."""

import pytest
import typst

from rxiv2pdf.jats import parse_jats
from rxiv2pdf.mathml import mathml_to_typst
from rxiv2pdf.render_typst import ImageRef, render

M = 'xmlns="http://www.w3.org/1998/Math/MathML"'

CASES = [
    (f"<math {M}><msub><mi>x</mi><mi>i</mi></msub></math>", "attach(x, b: i)"),
    (f"<math {M}><msup><mi>e</mi><mrow><mo>-</mo><mi>t</mi></mrow></msup></math>", "attach(e, t: - t)"),
    (f"<math {M}><mfrac><mi>a</mi><mn>2</mn></mfrac></math>", "frac(a, 2)"),
    (f"<math {M}><msqrt><mi>x</mi></msqrt></math>", "sqrt(x)"),
    (f"<math {M}><mroot><mi>x</mi><mn>3</mn></mroot></math>", "root(3, x)"),
    (f"<math {M}><mi>sin</mi><mi>θ</mi></math>", 'op("sin") θ'),
    (f"<math {M}><mi>Kd</mi></math>", 'upright("Kd")'),
    (f"<math {M}><mover><mi>x</mi><mo>¯</mo></mover></math>", "overline(x)"),
    (f"<math {M}><mover><mi>v</mi><mo>→</mo></mover></math>", "arrow(v)"),
    (f"<math {M}><munderover><mo>∑</mo><mrow><mi>i</mi><mo>=</mo><mn>1</mn></mrow><mi>n</mi></munderover></math>",
     "attach(limits(∑), b: i = 1, t: n)"),
    (f"<math {M}><mi>a</mi><mo>/</mo><mi>b</mi><mo>,</mo><mi>c</mi></math>", "a \\/ b \\, c"),  # '/' and ',' are special
    (f"<math {M}><mtext>if x &gt; 0</mtext></math>", '"if x > 0"'),
    (f"<math {M}><mfenced><mi>a</mi><mi>b</mi></mfenced></math>", "lr(\\( a \\, b \\))"),  # brackets escaped: they may be unbalanced across subtrees
    (f"<math {M}><mtable><mtr><mtd><mn>1</mn></mtd><mtd><mn>0</mn></mtd></mtr><mtr><mtd><mn>0</mn></mtd><mtd><mn>1</mn></mtd></mtr></mtable></math>",
     "mat(delim: #none, 1, 0; 0, 1)"),
    (f"<math {M}><mi mathvariant='bold'>F</mi></math>", "bold(F)"),
    (f"<math {M}><semantics><mi>y</mi><annotation encoding='TeX'>y</annotation></semantics></math>", "y"),
    # From real PLOS XML (PMC12204626): labelled under-brace, phantom spacer, word operator.
    (f"<math {M}><munder><munder><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow><mo>⏟</mo></munder><mtext>term</mtext></munder></math>",
     'attach(limits(underbrace(a + b)), b: "term")'),
    (f"<math {M}><mphantom><mo>(</mo></mphantom><mi>x</mi></math>", "x"),
    (f"<math {M}><munder><mo>min</mo><mo>Θ</mo></munder></math>", 'attach(limits(op("min")), b: Θ)'),
    (f"<math {M}><msub><mrow><mo>(</mo><mi>X</mi></mrow><mi>h</mi></msub></math>", "attach(\\( X, b: h)"),  # unbalanced
]


@pytest.mark.parametrize(("mathml", "expected"), CASES)
def test_conversion(mathml, expected):
    assert mathml_to_typst(mathml) == expected


def test_all_conversions_compile():
    body = "\n\n".join(f"${mathml_to_typst(m)}$ and $ {mathml_to_typst(m)} $" for m, _ in CASES)
    _, warnings = typst.compile_with_warnings(body.encode(), format="pdf")
    assert warnings == []


@pytest.mark.parametrize(
    "mathml",
    [
        f"<math {M}><maction actiontype='toggle'><mi>a</mi></maction></math>",  # unsupported element
        f"<math {M}><msub><mi>x</mi></msub></math>",  # wrong arity
        "<math><broken",  # not XML
    ],
)
def test_unsupported_returns_none(mathml):
    assert mathml_to_typst(mathml) is None


def _article(formula: str):
    from test_jats import jats

    return parse_jats(jats(body=f"<sec><title>S</title><p>We have {formula} here.</p></sec>"))


MML = '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:msub><mml:mi>K</mml:mi><mml:mi>d</mml:mi></mml:msub></mml:math>'


def test_renderer_prefers_mathml_then_image_then_tex():
    with_mml = _article(f'<inline-formula>{MML}<inline-graphic xlink:href="eq1.gif"/></inline-formula>')
    src, _ = render(with_mml, assets={"eq1.gif": ImageRef("figures/eq1.png", 40, 20)})
    assert "We have $attach(K, b: d)$ here" in src

    bad_mml = MML.replace("mml:msub", "mml:maction")
    image_only = _article(f'<inline-formula>{bad_mml}<inline-graphic xlink:href="eq1.gif"/></inline-formula>')
    src, _ = render(image_only, assets={"eq1.gif": ImageRef("figures/eq1.png", 40, 20)})
    assert '#inline-image("figures/eq1.png", px-height: 20, px-width: 40)' in src

    tex_only = _article("<inline-formula><tex-math>K_d</tex-math></inline-formula>")
    src, _ = render(tex_only, assets={})
    assert '#raw("K_d")' in src


def test_display_mathml_compiles(tmp_path):
    from test_jats import jats
    from test_render import compile_article

    article = parse_jats(jats(body=f'<sec><title>S</title><p>Defined as<disp-formula id="e1">{MML}</disp-formula>where d is.</p></sec>'))
    source, _, warnings = compile_article(article, tmp_path)
    assert '#display-math($ attach(K, b: d) $, segments: ("attach(K, b: d)",)) <eq-e1>' in source and warnings == []


def test_wide_and_narrow_inline_formula_images_compile(tmp_path):
    from PIL import Image
    from test_jats import jats
    from test_render import compile_article

    (tmp_path / "figures").mkdir()
    for name, size in (("wide", (719, 32)), ("narrow", (50, 29)), ("frac", (317, 47))):
        Image.new("RGB", size, (30, 30, 30)).save(tmp_path / f"figures/{name}.png")
    body = "".join(
        f'<p>Text <inline-formula><inline-graphic xlink:href="{n}.gif"/></inline-formula>where it ends.</p>'
        for n in ("wide", "narrow", "frac")
    )
    article = parse_jats(jats(body=f"<sec><title>S</title>{body}</sec>"))
    assets = {f"{n}.gif": ImageRef(f"figures/{n}.png", *s) for n, s in (("wide", (719, 32)), ("narrow", (50, 29)), ("frac", (317, 47)))}
    source, _, warnings = compile_article(article, tmp_path, assets=assets)
    assert 'px-height: 32, px-width: 719' in source and warnings == []


def test_display_segments_break_only_before_operators_outside_brackets():
    from rxiv2pdf.mathml import mathml_to_segments

    # q(n) = P(a | b) + P(c), with loose bracket tokens as PLOS writes them
    mml = (f"<math {M}><mi>q</mi><mo>(</mo><mi>n</mi><mo>)</mo><mo>=</mo><mi>P</mi><mo>(</mo><mi>a</mi>"
           "<mo>|</mo><mi>b</mi><mo>)</mo><mo>+</mo><mi>P</mi><mo>(</mo><mi>c</mi><mo>)</mo></math>")
    segs = mathml_to_segments(mml)
    assert segs == ["q \\( n \\)", "= P \\( a \\| b \\)", "+ P \\( c \\)"]
    assert " ".join(segs) == mathml_to_typst(mml)


def test_long_display_equation_breaks_into_lines(tmp_path):
    from test_jats import jats
    from test_render import compile_article

    terms = "<mo>+</mo>".join(f"<msub><mi>a</mi><mn>{i}</mn></msub><msup><mi>x</mi><mn>{i}</mn></msup>" for i in range(40))
    article = parse_jats(jats(body=f'<sec><title>S</title><p>Sum<disp-formula id="e1"><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>y</mi><mo>=</mo>{terms}</math></disp-formula>done.</p></sec>'))
    source, _, warnings = compile_article(article, tmp_path)
    assert source.count('"+ attach(a, b: ') == 39 and warnings == []
