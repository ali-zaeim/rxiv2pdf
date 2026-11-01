"""Visual check: render page 1 of every test fixture to PNG.

    uv run python scripts/preview.py [OUTDIR]      (default: ./previews)

Figures are not stored with the fixtures, so grey placeholder images stand in for them.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import typst
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_render import graphic_hrefs

from rxiv2pdf.compile import font_dirs, install_template
from rxiv2pdf.jats import parse_jats
from rxiv2pdf.render_typst import ImageRef, render


def main() -> None:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "previews")
    outdir.mkdir(parents=True, exist_ok=True)
    for xml in sorted((ROOT / "tests" / "fixtures").glob("*/v*.source.xml")):
        article = parse_jats(xml.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "figures").mkdir()
            assets = {}
            for href in graphic_hrefs(article):
                rel = f"figures/{href.replace('/', '_')}.png"
                Image.new("RGB", (800, 500), (200, 200, 210)).save(build / rel)
                assets[href] = ImageRef(rel, 800, 500)
            source, _ = render(article, assets=assets)
            install_template(build)
            (build / "doc.typ").write_text(source, encoding="utf-8")
            png = typst.compile(
                str(build / "doc.typ"), root=str(build), format="png", ppi=110,
                font_paths=font_dirs(), ignore_system_fonts=True,
            )
            first = png[0] if isinstance(png, list) else png
            target = outdir / f"{xml.parent.name}-p1.png"
            target.write_bytes(first)
            print(target)


if __name__ == "__main__":
    main()
