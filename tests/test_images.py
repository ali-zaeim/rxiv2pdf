from PIL import Image

from rxiv2pdf.images import normalize, sniff_ext


def test_jpeg_within_limit_untouched(tmp_path):
    p = tmp_path / "f.jpg"
    Image.new("RGB", (800, 600)).save(p, format="JPEG")
    before = p.read_bytes()
    info = normalize(p)
    assert info.path == p and not info.converted and not info.downscaled
    assert (info.width, info.height) == (800, 600)
    assert p.read_bytes() == before


def test_tiff_converted_to_png(tmp_path):
    p = tmp_path / "f.tif"
    Image.new("RGB", (120, 80), (10, 200, 10)).save(p, format="TIFF")
    info = normalize(p)
    assert info.path == tmp_path / "f.png" and info.format == "PNG" and info.original_format == "TIFF"
    assert info.converted and not p.exists()
    with Image.open(info.path) as im:
        assert im.format == "PNG" and im.size == (120, 80)


def test_cmyk_tiff_converted(tmp_path):
    p = tmp_path / "cmyk.tif"
    Image.new("CMYK", (50, 40)).save(p, format="TIFF")
    info = normalize(p)
    with Image.open(info.path) as im:
        assert im.format == "PNG" and im.mode == "RGB"


def test_16bit_tiff_converted(tmp_path):
    p = tmp_path / "deep.tif"
    Image.new("I;16", (30, 20), 40000).save(p, format="TIFF")
    info = normalize(p)
    with Image.open(info.path) as im:
        assert im.format == "PNG" and im.mode == "L"
        assert im.getpixel((0, 0)) == 40000 // 256


def test_large_jpeg_downscaled_long_edge(tmp_path):
    p = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 1500)).save(p, format="JPEG")
    info = normalize(p, max_edge=3000)
    assert info.path == p and info.downscaled and not info.converted
    assert (info.width, info.height) == (3000, 750)


def test_large_tiff_converted_and_downscaled(tmp_path):
    p = tmp_path / "big.tif"
    Image.new("RGB", (1000, 4000)).save(p, format="TIFF")
    info = normalize(p, max_edge=3000)
    assert info.path.suffix == ".png" and info.converted and info.downscaled
    assert (info.width, info.height) == (750, 3000)


def test_sniff_ext():
    assert sniff_ext(b"\xff\xd8\xff\xe0") == ".jpg"
    assert sniff_ext(b"\x89PNG\r\n\x1a\n") == ".png"
    assert sniff_ext(b"II*\x00") == ".tif"
    assert sniff_ext(b"????", "image/gif") == ".gif"
    assert sniff_ext(b"????", None) == ".bin"


def test_text_line_height_of_a_table_screenshot(tmp_path):
    from PIL import ImageDraw

    from rxiv2pdf.images import text_line_height

    im = Image.new("L", (1280, 420), 255)
    d = ImageDraw.Draw(im)
    for i, y in enumerate(range(30, 400, 40)):  # nine lines of "words", 22 px tall
        for x in range(60 + i * 37 % 60, 1200, 90):  # staggered, like real text
            d.rectangle([x, y, x + 30, y + 21], fill=0)
    d.line([(10, 0), (10, 419)], fill=0, width=2)  # cell border: not text
    d.line([(0, 20), (1279, 20)], fill=0, width=2)  # rule: not text
    im.save(tmp_path / "t.png")
    assert text_line_height(tmp_path / "t.png") == 22
    Image.new("L", (1280, 420), 255).save(tmp_path / "blank.png")
    assert text_line_height(tmp_path / "blank.png") is None


def _chart(tmp_path, size_pt: int, ppi: int = 144):
    """A chart-like image: axis, coloured bars and heat-map cells, grey lettering at a known size."""
    import typst

    src = f"""#set page(width: 600pt, height: auto, margin: 12pt)
#set text(size: {size_pt}pt, fill: luma(40))
#grid(columns: (auto, 1fr), gutter: 8pt,
  align(horizon, rotate(-90deg, reflow: true)[Response (a.u.)]),
  stack(spacing: 4pt,
    grid(columns: 8, gutter: 3pt, ..range(32).map(i => rect(width: 40pt, height: 14pt, fill: if calc.even(i) {{ rgb("#2b6cb0") }} else {{ rgb("#c53030") }}))),
    line(length: 100%),
    grid(columns: 6, gutter: 1fr, [0.0], [0.5], [1.0], [1.5], [2.0], [2.5]),
    align(center)[Temperature (°C)],
  ),
)
Mobile #h(1em) Immobile #h(1em) Control #h(1em) p = 0.003"""
    path = tmp_path / f"chart{size_pt}.png"
    path.write_bytes(typst.compile(src.encode(), format="png", ppi=ppi))
    return path, size_pt * ppi / 72


def test_lettering_height_of_a_chart(tmp_path):
    from rxiv2pdf.images import GLYPH_PER_EM, lettering_height

    for size in (8, 14, 20):
        path, em_px = _chart(tmp_path, size)
        g = lettering_height(path)
        assert g is not None and 0.8 < g / (GLYPH_PER_EM * em_px) < 1.25, (size, g, em_px)


def test_lettering_height_ignores_coloured_marks_and_photos(tmp_path):
    from PIL import ImageDraw

    from rxiv2pdf.images import lettering_height

    im = Image.new("RGB", (1280, 700), "white")
    d = ImageDraw.Draw(im)
    for i in range(20):  # dark-blue heat-map cells in rows: glyph-sized, but coloured
        for j in range(10):
            d.rectangle([40 + i * 60, 40 + j * 60, 80 + i * 60, 90 + j * 60], fill=(40, 70, 150))
    im.save(tmp_path / "heat.png")
    assert lettering_height(tmp_path / "heat.png") is None
    # A micrograph's grain can pass for small type; that is harmless (a small reading only
    # allows a figure to be wider), but it must never read as large lettering (would shrink it).
    Image.effect_noise((1280, 900), 90).save(tmp_path / "noise.png")
    g = lettering_height(tmp_path / "noise.png")
    assert g is None or g < 14
