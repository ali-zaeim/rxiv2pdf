"""Stage 5 - Compile: write the build directory and run the Typst compiler (``typst`` package)."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import typst
from PIL import Image

from .fetch import Manifest
from .images import lettering_height, text_line_height
from .model import Article
from .render_typst import TEMPLATE_NAME, AppendixItem, ImageRef, RenderOptions, render

log = logging.getLogger(__name__)


class CompileError(Exception):
    """Typst rejected the generated source. ``str(err)`` is meant for the user."""


@dataclass
class BuildResult:
    pdf: Path
    warnings: list[str]  # renderer + Typst warnings
    appended: list[tuple[str, int]] = field(default_factory=list)  # (file, pages) put after the article
    not_appended: list[str] = field(default_factory=list)  # supplementary files that are not PDFs/images


def template_dir() -> Path:
    return Path(str(resources.files("rxiv2pdf") / "templates"))


def font_dirs() -> list[str]:
    """Bundled fonts. Typst's embedded fonts are always available."""
    fonts = Path(str(resources.files("rxiv2pdf"))) / "fonts"
    return [str(fonts)] if fonts.is_dir() else []


def install_template(build: Path) -> None:
    """Copy journal.typ and themes/ into the build directory."""
    src = template_dir()
    shutil.copyfile(src / TEMPLATE_NAME, build / TEMPLATE_NAME)
    shutil.copytree(src / "themes", build / "themes", dirs_exist_ok=True)


PROMOTE_LABEL = "rxiv-promote"
_PROMOTE_ROUNDS = 4
BALANCE_INPUT = "rxiv-balance"
# Extra height, in body lines, given to the balanced columns: the left column can end up to a
# line short (a line or a heading does not fit), and the right one must still take the rest.
_BALANCE_SLACK = (0.5, 1.5)
REF_SCALE_INPUT = "rxiv-ref-scale"
_REF_SCALES = (0.97, 0.94, 0.9)  # 0.9: the compact theme's reference size
_SHORT_TAIL = 0.2  # a last page whose left column is filled at most this much (and right empty)


def _end_of_body(compiler: typst.Compiler) -> dict | None:
    """Where the body ends in the last compile (journal.typ's ``<rxiv-end>``), in pt."""
    ends = json.loads(compiler.query("<rxiv-end>", field="value"))
    return ends[0] if len(ends) == 1 else None


def _pull_back_short_tail(compiler: typst.Compiler, pdf: Path, inputs: dict[str, str]) -> dict[str, str]:
    """If the last page holds only a few lines, try to fit them on the page before by setting
    the reference list a little smaller (``rxiv-ref-scale``). Returns the inputs now in effect
    (the compiler is left compiled with them)."""
    end = _end_of_body(compiler)
    if end is None or end["page"] == 1 or end["col"] != 0 or end["floats"] or end["used"] > _SHORT_TAIL * end["avail"]:
        return inputs
    trial = pdf.with_name(pdf.stem + ".tightening.pdf")
    try:
        for scale in _REF_SCALES:
            tight = {**inputs, REF_SCALE_INPUT: str(scale)}
            _, warnings = compiler.compile_with_warnings(output=str(trial), sys_inputs=tight)
            after = _end_of_body(compiler)
            if not warnings and after is not None and after["page"] == end["page"] - 1:
                trial.replace(pdf)
                return tight
        compiler.compile_with_warnings(output=str(trial), sys_inputs=inputs)  # back to the kept layout
        return inputs
    finally:
        trial.unlink(missing_ok=True)


def compile_typst(typ: Path, pdf: Path, *, root: Path) -> list[str]:
    """Compile ``typ`` to ``pdf`` with system fonts disabled (deterministic output).

    journal.typ promotes a column float that ran off the page to a page-wide one, marking it
    with ``<rxiv-promote>`` metadata (see ``_float``). A promotion moves things around (the next
    float can run off the page, the page count changes), and Typst stops after five layout
    passes. If the layout had not converged, the floats marked so far are passed back in
    (``sys.inputs``), promoted from the first pass on, and the document is laid out again.
    Then the columns of the last page are balanced (``_balance_last_page``).
    """
    try:
        compiler = typst.Compiler(str(typ), root=str(root), font_paths=font_dirs(), ignore_system_fonts=True)
        promoted: set[int] = set()
        for _ in range(_PROMOTE_ROUNDS):
            inputs = {PROMOTE_LABEL: ",".join(str(k) for k in sorted(promoted))}
            _, warnings = compiler.compile_with_warnings(output=str(pdf), sys_inputs=inputs)
            if not any("converge" in _format_warning(w) for w in warnings):
                break
            marked = {int(k) for k in json.loads(compiler.query(f"<{PROMOTE_LABEL}>", field="value"))}
            if marked <= promoted:
                break
            promoted |= marked
        if not warnings:  # only a layout that converged
            inputs = _pull_back_short_tail(compiler, pdf, inputs)
            _balance_last_page(compiler, pdf, inputs)
    except typst.TypstError as exc:
        detail = getattr(exc, "diagnostic", None) or str(exc)
        raise CompileError(f"Typst failed to compile {typ.name}: {detail}") from exc
    return [_format_warning(w) for w in warnings]


def _balance_last_page(compiler: typst.Compiler, pdf: Path, inputs: dict[str, str]) -> bool:
    """Even out the two columns on the last page of the body (Typst fills the left one first).

    journal.typ reports where the body ends (``<rxiv-end>``: page, column, height used, column
    height, line pitch, floats on that page, in pt). The text there is split in half by an
    empty page-wide float at the bottom of that page (``rxiv-balance``: its height) that
    shortens both columns. The balanced PDF replaces ``pdf`` only if the body still ends on
    that page, and with no layout warnings. Pages with floats are left alone (their columns
    do not start at the top). Returns whether the PDF was balanced.
    """
    end = _end_of_body(compiler)
    if end is None or end["page"] == 1 or end["floats"]:
        return False
    total = end["used"] if end["col"] == 0 else end["avail"] + end["used"]
    trial = pdf.with_name(pdf.stem + ".balancing.pdf")
    try:
        for slack in _BALANCE_SLACK:
            spacer = end["avail"] - (total / 2 + slack * end["pitch"])
            if spacer < 2 * end["pitch"]:
                break  # already (nearly) even
            _, warnings = compiler.compile_with_warnings(output=str(trial), sys_inputs={**inputs, BALANCE_INPUT: f"{spacer:.3f}"})
            after = _end_of_body(compiler)
            spacer_pages = json.loads(compiler.query("<rxiv-spacer>", field="value"))
            if not warnings and after is not None and after["page"] == end["page"] and spacer_pages == [end["page"]]:
                trial.replace(pdf)
                return True
        return False
    finally:
        trial.unlink(missing_ok=True)


def _format_warning(w: object) -> str:
    message = getattr(w, "message", str(w))
    hints = getattr(w, "hints", None) or []
    return message + (f" (hint: {'; '.join(hints)})" if hints else "")


_APPEND_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_CONVERT_IMAGES = {".tif", ".tiff", ".bmp"}
_MAX_PROBE_PAGES = 5000


def pdf_page_count(path: Path) -> int | None:
    """Pages Typst can place from ``path`` (None if it cannot read the PDF at all).

    Typst has no page-count query, but placing a page that does not exist is an error, so
    the count is found by probing (exponential, then binary search: ~2 log2(n) tiny compiles).
    """

    def has(n: int) -> bool:
        try:
            typst.compile(f'#image("{path.name}", page: {n})'.encode(), root=str(path.parent), format="svg")
        except typst.TypstError:
            return False
        return True

    if not has(1):
        return None
    lo, hi = 1, 2
    while hi <= _MAX_PROBE_PAGES and has(hi):
        lo, hi = hi, hi * 2
    hi = min(hi, _MAX_PROBE_PAGES + 1)
    while hi - lo > 1:  # has(lo), not has(hi)
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if has(mid) else (lo, mid)
    return lo


def _prepare_appendix(manifest: Manifest, version_dir: Path, build: Path) -> tuple[list[AppendixItem], list[str], list[str]]:
    """Copy downloaded supplementary PDFs/images into ``build`` for appending.

    Returns (items, files that are not PDFs/images, warnings)."""
    items: list[AppendixItem] = []
    skipped: list[str] = []
    warnings: list[str] = []
    for s in manifest.supplements:
        if s.status != "ok" or not s.path or not (version_dir / s.path).exists():
            continue
        src = version_dir / s.path
        ext = src.suffix.lower()
        label = s.label or "Supplementary file"
        dest = build / "supplements" / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if ext == ".pdf":
            shutil.copyfile(src, dest)
            pages = pdf_page_count(dest)
            if pages is None:
                warnings.append(f"Supplementary file {src.name} could not be read as a PDF, so it was not appended")
                continue
        elif ext in _APPEND_IMAGES:
            shutil.copyfile(src, dest)
            pages = 1
        elif ext in _CONVERT_IMAGES:
            dest = dest.with_suffix(".png")
            try:
                with Image.open(src) as im:
                    im.convert("RGBA" if "A" in im.mode else "RGB").save(dest)
            except OSError:
                warnings.append(f"Supplementary image {src.name} could not be read, so it was not appended")
                continue
            pages = 1
        else:
            skipped.append(src.name)
            continue
        items.append(AppendixItem(label, src.name, dest.relative_to(build).as_posix(), pages, s.href))
    return items, skipped, warnings


def _text_px(kind: str | None, path: Path) -> float | None:
    """Size of the text in an image, in pixels, that the template sizes it by (see ImageRef)."""
    if kind == "table":
        return text_line_height(path)
    if kind == "figure":
        return lettering_height(path)
    return None


def build_pdf(
    article: Article,
    manifest: Manifest,
    version_dir: Path,
    outdir: Path,
    *,
    options: RenderOptions | None = None,
    append_supplements: bool = False,
) -> BuildResult:
    """Render ``article`` and compile ``outdir/<slug>.pdf``.

    The .typ, the template and ``figures/`` live in a temporary build directory; if Typst
    fails, they are copied to ``outdir/<slug>-typst-debug/`` for the bug report. With
    ``append_supplements``, downloaded supplementary PDFs and images follow the article.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    assets = {f.href: ImageRef(f.path, f.width, f.height, _text_px(f.kind, version_dir / f.path)) for f in manifest.figures if f.status == "ok" and f.path}
    supplement_urls = {s.href: s.url for s in manifest.supplements if s.url}

    with tempfile.TemporaryDirectory(prefix="rxiv2pdf-") as tmp:
        build = Path(tmp)
        appendix, not_appended, warnings = (
            _prepare_appendix(manifest, version_dir, build) if append_supplements else ([], [], [])
        )
        source, render_warnings = render(
            article, assets=assets, options=options, supplement_urls=supplement_urls, appendix=appendix
        )
        warnings = render_warnings + warnings
        install_template(build)
        for rel in {ref.path for ref in assets.values()}:
            dest = build / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(version_dir / rel, dest)
        typ = build / f"{manifest.slug}.typ"
        typ.write_text(source, encoding="utf-8")
        pdf = outdir / f"{manifest.slug}.pdf"
        try:
            warnings += compile_typst(typ, pdf, root=build)
        except CompileError as exc:
            debug = outdir / f"{manifest.slug}-typst-debug"
            shutil.copytree(build, debug, dirs_exist_ok=True)
            raise CompileError(f"{exc}\nThis is a bug in rxiv2pdf; the generated source was kept in {debug} - please report it with that folder.") from exc
        return BuildResult(pdf, warnings, [(x.file, x.pages) for x in appendix], not_appended)


__all__ = ["TEMPLATE_NAME", "BuildResult", "CompileError", "build_pdf", "compile_typst"]
