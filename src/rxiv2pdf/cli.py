"""Command-line entry point: resolve -> fetch -> parse -> render -> compile."""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__
from .compile import CompileError, build_pdf
from .fetch import FetchResult, JatsUnavailable, fetch, fetch_original_pdf
from .jats import parse_jats
from .net import Client, FetchError
from .render_typst import RenderOptions
from .resolve import ResolveError, resolve

console = Console(stderr=True)
out = Console()

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


class ServerChoice(str, Enum):
    biorxiv = "biorxiv"
    medrxiv = "medrxiv"


class PaperChoice(str, Enum):
    letter = "letter"
    a4 = "a4"


def make_client() -> Client:
    """Factory so tests can swap in a mocked transport."""
    return Client()


def _setup_logging(verbose: int) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = RichHandler(console=console, show_time=False, show_path=verbose > 1, markup=False)
    logging.basicConfig(level=logging.WARNING, handlers=[handler], format="%(message)s", force=True)
    logging.getLogger("rxiv2pdf").setLevel(level)
    if verbose > 1:
        logging.getLogger("httpx").setLevel(logging.INFO)


class ThemeChoice(str, Enum):
    default = "default"
    compact = "compact"
    sans = "sans"


@dataclass
class _Options:
    outdir: Path
    version: int | None
    server: str | None
    refresh: bool
    supplements: bool
    paper: str
    theme: str
    verbose: int
    fallback_pdf: bool = False
    open_pdf: bool = False
    append_supplements: bool = False


class _ItemFailed(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"rxiv2pdf {__version__}")
        raise typer.Exit()


@app.command(no_args_is_help=True)  # on the command: a one-command app ignores it on Typer()
def run(
    source: Annotated[str | None, typer.Argument(help="bioRxiv/medRxiv URL or DOI (10.1101/... or 10.64898/...)")] = None,
    list_file: Annotated[Path | None, typer.Option("--list", exists=True, dir_okay=False, help="Batch mode: a file with one URL/DOI per line (# comments allowed).")] = None,
    outdir: Annotated[Path, typer.Option("-o", "--outdir", help="Output directory.")] = Path("."),
    preprint_version: Annotated[int | None, typer.Option("--preprint-version", min=1, help="Preprint version (default: from URL, else latest).")] = None,
    server: Annotated[ServerChoice | None, typer.Option("--server", help="Override server detection.")] = None,
    theme: Annotated[ThemeChoice, typer.Option("--theme", help="Look of the PDF.")] = ThemeChoice.default,
    paper: Annotated[PaperChoice, typer.Option("--paper", help="Paper size.")] = PaperChoice.letter,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore the cache and re-download.")] = False,
    supplements: Annotated[bool, typer.Option("--supplements", help="Also download the supplementary files, into <name>-supplements/ next to the PDF (listed and linked in the PDF either way).")] = False,
    append_supplements: Annotated[bool, typer.Option("--append-supplements", help="Append the supplementary PDFs and images to the end of the PDF (downloads them).")] = False,
    fallback_pdf: Annotated[bool, typer.Option("--fallback-pdf", help="If no JATS XML exists yet, save the server's original PDF instead.")] = False,
    open_pdf: Annotated[bool, typer.Option("--open", help="Open the PDF when done.")] = False,
    verbose: Annotated[int, typer.Option("-v", "--verbose", count=True, metavar="", show_default=False, help="-v: per-figure detail and tracebacks; -vv: also every HTTP request.")] = 0,
    show_version: Annotated[bool, typer.Option("--version", callback=_show_version, is_eager=True, help="Show the rxiv2pdf version and exit.")] = False,
) -> None:
    """Build a journal-style PDF of a bioRxiv/medRxiv preprint from its JATS XML."""
    _setup_logging(verbose)
    if (source is None) == (list_file is None):
        console.print("[bold red]error:[/] give either a URL/DOI or --list FILE")
        raise typer.Exit(2)
    if list_file is not None and preprint_version is not None:
        console.print("[bold red]error:[/] --preprint-version applies to a single preprint; put versions in the list (e.g. ...575123v2)")
        raise typer.Exit(2)
    opts = _Options(
        outdir, preprint_version, server.value if server else None, refresh, supplements,
        paper.value, theme.value, verbose, fallback_pdf, open_pdf, append_supplements,
    )
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"[bold red]error:[/] cannot create the output directory {outdir}: {exc.strerror or exc}")
        raise typer.Exit(2) from exc

    try:
        with make_client() as client:
            if source is not None:
                try:
                    _process(source, client, opts)
                except _ItemFailed as exc:
                    console.print(f"[bold red]error:[/] {exc}")
                    raise typer.Exit(exc.code) from exc
                return
            assert list_file is not None
            _batch(list_file, client, opts)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/] Everything downloaded so far is cached; re-run to resume.")
        raise typer.Exit(130) from None


def _batch(list_file: Path, client: Client, opts: _Options) -> None:
    items = [line.strip() for line in list_file.read_text(encoding="utf-8").splitlines()]
    items = [i for i in items if i and not i.startswith("#")]
    if not items:
        console.print(f"[bold red]error:[/] no URLs/DOIs in {list_file}")
        raise typer.Exit(2)
    results: list[tuple[str, bool, str]] = []
    for n, item in enumerate(items, 1):
        out.rule(f"[{n}/{len(items)}] {item}")
        try:
            pdf = _process(item, client, opts)
            results.append((item, True, str(pdf)))
        except _ItemFailed as exc:
            console.print(f"[bold red]error:[/] {exc}")
            results.append((item, False, str(exc)))
    table = Table(title=f"Batch: {sum(ok for _, ok, _ in results)}/{len(results)} succeeded", show_lines=False)
    table.add_column("Preprint", overflow="fold")
    table.add_column("Result", overflow="fold")
    for item, ok, detail in results:
        table.add_row(item, f"[green]{detail}[/]" if ok else f"[red]failed:[/] {detail}")
    out.print(table)
    if not all(ok for _, ok, _ in results):
        raise typer.Exit(1)


def _process(source: str, client: Client, o: _Options) -> Path:
    """Run the whole pipeline for one preprint. Returns the PDF path."""
    try:
        resolved = resolve(source, server=o.server, version=o.version)
    except ResolveError as exc:
        raise _ItemFailed(str(exc), 2) from exc
    try:
        result = fetch(resolved, client=client, refresh=o.refresh, supplements=o.supplements or o.append_supplements)
    except JatsUnavailable as exc:
        if not o.fallback_pdf:
            raise _ItemFailed(str(exc)) from exc
        return _fallback(resolved, client, o)
    except FetchError as exc:
        raise _ItemFailed(str(exc)) from exc

    _print_summary(result, verbose=o.verbose)

    try:
        article = parse_jats(result.xml_path.read_bytes(), meta=result.manifest)
        build = build_pdf(
            article, result.manifest, result.version_dir, o.outdir,
            options=RenderOptions(paper=o.paper, theme=o.theme),
            append_supplements=o.append_supplements,
        )
    except CompileError as exc:
        raise _ItemFailed(str(exc)) from exc
    except OSError as exc:
        raise _ItemFailed(f"could not write to {o.outdir}: {exc.strerror or exc}") from exc
    except Exception as exc:  # a parser/renderer bug should name the preprint, not just a traceback
        if o.verbose:
            console.print_exception()
        raise _ItemFailed(
            f"rxiv2pdf could not build a PDF for {result.manifest.doi} v{result.manifest.version} "
            f"({type(exc).__name__}: {exc}). This is a bug in rxiv2pdf, not in your input; please report it"
            + ("" if o.verbose else " (re-run with -v for a traceback).")
        ) from exc

    for correction in article.corrections:
        console.print(f"[cyan]fixed:[/] {correction}")
    for warning in build.warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    notes = []
    if article.warnings:
        notes.append(f"{len(article.warnings)} parse warning(s)")
    if build.warnings:
        notes.append(f"{len(build.warnings)} render/compile warning(s)")
    # soft_wrap: never hard-wrap paths, so they stay copy-pasteable
    out.print(f"[bold green]PDF[/]  {build.pdf}" + (f"  ({', '.join(notes)})" if notes else ""), soft_wrap=True)
    if o.append_supplements:
        if build.appended:
            pages = sum(n for _, n in build.appended)
            files = ", ".join(f"{name} ({n} p.)" for name, n in build.appended)
            out.print(f"[bold green]Appended[/] {len(build.appended)} supplementary file(s), {pages} page(s): {files}", soft_wrap=True)
        elif not result.manifest.supplements:
            out.print("[dim]No supplementary files listed for this preprint.[/]")
        if build.not_appended:
            console.print(
                f"[yellow]note:[/] not PDFs or images, so not appended (linked in the PDF): {', '.join(build.not_appended)}"
                + ("" if o.supplements else "; --supplements saves them next to the PDF"),
                soft_wrap=True,
            )
    if o.supplements:
        _deliver_supplements(result, o.outdir)
    if o.open_pdf:
        _open(build.pdf)
    return build.pdf


def _deliver_supplements(result: FetchResult, outdir: Path) -> None:
    """Copy the downloaded supplementary files from the cache to <outdir>/<slug>-supplements/."""
    m = result.manifest
    if not m.supplements:
        out.print("[dim]No supplementary files listed for this preprint.[/]")
        return
    dest_dir = outdir / f"{m.slug}-supplements"
    for s in m.supplements:
        src = result.version_dir / s.path if s.status == "ok" and s.path else None
        if src is None or not src.exists():
            console.print(f"[yellow]warning:[/] supplementary file {s.label or s.href} could not be downloaded")
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest_dir / src.name)
        except OSError as exc:
            raise _ItemFailed(f"could not write to {dest_dir}: {exc.strerror or exc}") from exc
        out.print(f"[bold green]SUPP[/] {dest_dir / src.name}" + (f"  ({s.label})" if s.label else ""), soft_wrap=True)


def _fallback(resolved: object, client: Client, o: _Options) -> Path:
    """--fallback-pdf: no JATS yet, so hand over the server's own PDF."""
    try:
        cached, slug = fetch_original_pdf(resolved, client=client)  # type: ignore[arg-type]
    except FetchError as exc:
        raise _ItemFailed(f"No JATS XML yet, and the original PDF could not be fetched either: {exc}") from exc
    dest = o.outdir / f"{slug}.pdf"
    shutil.copyfile(cached, dest)
    console.print("[yellow]warning:[/] no JATS XML is available yet, so this is the server's original PDF, not a rebuilt one")
    out.print(f"[bold green]PDF[/]  {dest}  (original, --fallback-pdf)", soft_wrap=True)
    if o.open_pdf:
        _open(dest)
    return dest


def _open(path: Path) -> None:
    import os
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:
        console.print(f"[yellow]warning:[/] could not open {path}: {exc}")


def _print_summary(result: FetchResult, *, verbose: int) -> None:
    m = result.manifest
    figs = [f for f in m.figures if f.kind == "figure"]
    tables = [f for f in m.figures if f.kind == "table"]
    other = [f for f in m.figures if f.kind not in ("figure", "table")]
    missing = [f for f in m.figures if f.status != "ok"]
    strategies = Counter(f.strategy for f in m.figures if f.status == "ok")

    def ok(items: list) -> str:
        return f"{sum(1 for f in items if f.status == 'ok')}/{len(items)}"

    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold")
    t.add_column(overflow="fold")
    t.add_row("Preprint", f"{m.server} {m.doi} v{m.version} (versions: {', '.join(f'v{v}' for v in m.versions_available)})")
    t.add_row("Title", m.title)
    t.add_row("Posted", f"{m.date} | {m.category} | {m.type} | license {m.license}")
    if m.withdrawn:
        t.add_row("[red]Withdrawn[/]", f"[red]on {m.withdrawn.get('date')} (v{m.withdrawn.get('version')}); showing v{m.version} with a banner[/]")
    if m.published and m.published != "NA":
        t.add_row("Published", m.published)
    t.add_row("JATS", f"{result.xml_path.stat().st_size // 1024} KB via {m.jats_strategy}")
    t.add_row(
        "Graphics",
        f"figures {ok(figs)}, table images {ok(tables)}"
        + (f", other {ok(other)}" if other else "")
        + (f"  [red]({len(missing)} missing)[/]" if missing else ""),
    )
    if strategies:
        t.add_row("Strategies", ", ".join(f"{name}: {n}" for name, n in strategies.most_common()))
    downloaded = sum(1 for s in m.supplements if s.status == "ok")
    t.add_row("Supplements", f"{len(m.supplements)} listed, {downloaded} downloaded" + ("" if downloaded else " (--supplements to download)"))
    n = result.requests_made  # a cache hit can still look up the version list (API)
    net = f"{n} network request{'' if n == 1 else 's'}"
    if result.from_cache:
        net = f"cache hit, {net}"
    t.add_row("Cache", f"{result.version_dir}  ({net})")
    out.print(t)

    if verbose:
        detail = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
        for col in ("label", "kind", "hwp ids", "status", "strategy", "size"):
            detail.add_column(col, no_wrap=True)
        detail.add_column("file", overflow="fold")
        for f in m.figures:
            detail.add_row(
                f.label or "",
                f.kind,
                f"{f.parent_id}/{f.graphic_id}",
                f.status,
                f.strategy or "",
                f"{f.width}x{f.height}" if f.width else "",
                f.path or f.href,
            )
        out.print(detail)




def main() -> None:
    app()


if __name__ == "__main__":
    main()
