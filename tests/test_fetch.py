import json
import re
from pathlib import Path

import httpx
import pytest
from conftest import EQN_DOI, NEW_DIR, NEW_DOI, OLD_DIR, OLD_DOI, FakeRxiv
from typer.testing import CliRunner

from rxiv2pdf import cli
from rxiv2pdf.fetch import JatsUnavailable, PreprintNotFound, VersionNotFound, fetch
from rxiv2pdf.net import RateLimited
from rxiv2pdf.resolve import resolve

OLD_BASE = "https://www.biorxiv.org/content/biorxiv/early/2020/03/27/2020.03.22.002386"
NEW_BASE = "https://www.biorxiv.org/content/biorxiv/early/2026/10/19/2026.10.16.759679"


def run(fake: FakeRxiv, cache, raw, **kwargs):
    resolve_kwargs = {k: kwargs.pop(k) for k in ("server", "version") if k in kwargs}
    with fake.client() as client:
        return fetch(resolve(raw, **resolve_kwargs), client=client, cache_root=cache, **kwargs)


# --- old 10.1101 paper: API jatsxml redirect, figures + multi-image tables ------------------
def test_old_paper_end_to_end(fake, cache):
    res = run(fake, cache, f"https://www.biorxiv.org/content/{OLD_DOI}v3.full")
    m = res.manifest

    assert (m.server, m.doi, m.version, m.versions_available) == ("biorxiv", OLD_DOI, 3, [1, 2, 3])
    assert m.slug == "2020.03.22.002386v3"
    assert m.jats_strategy == "api-jatsxml"
    assert m.jats_url == f"{OLD_BASE}.source.xml"  # final URL after the 301
    assert m.content_base == OLD_BASE
    assert m.license == "cc_by" and m.published == "10.1038/s41586-020-2286-9"
    assert res.xml_path.read_bytes() == (OLD_DIR / "v3.source.xml").read_bytes()
    assert not res.from_cache

    figs = [f for f in m.figures if f.kind == "figure"]
    tables = [f for f in m.figures if f.kind == "table"]
    assert len(figs) == 5 and len(tables) == 10
    assert all(f.status == "ok" and f.strategy == "hwp-slug" for f in m.figures)

    f1 = figs[0]
    assert (f1.parent_id, f1.graphic_id, f1.parent_xml_id, f1.label) == ("F1", "graphic-1", "fig1", "Figure 1:")
    assert f1.source_url == f"{OLD_BASE}/F1.large.jpg"
    assert f1.path == "figures/002386v3_fig1.jpg" and (res.version_dir / f1.path).exists()
    assert (f1.width, f1.height, f1.format) == (40, 30, "JPEG")

    t1 = [t for t in tables if t.parent_id == "T1"]
    assert [t.graphic_id for t in t1] == [f"graphic-{i}" for i in range(5, 10)]
    assert t1[1].source_url == f"{OLD_BASE}/T1/graphic-6.large.jpg"
    assert t1[0].label == "Table 1a."

    # Supplements are listed (with URLs) but not downloaded by default.
    assert len(m.supplements) == 10
    s1 = m.supplements[0]
    assert (s1.id, s1.media_id, s1.href, s1.status) == ("DC1", "media-1", "supplements/002386_file07.pdf", "listed")
    assert s1.url == f"{OLD_BASE}/DC1/embed/media-1.pdf"
    assert not (res.version_dir / "supplements").exists()

    # 1 API + 2 XML (301 + 200) + 15 images; no HTML page needed, no wasted guesses.
    assert res.requests_made == 18 == len(fake.requests)
    assert not any(u.endswith(".full") for u in fake.requests)

    on_disk = json.loads((res.version_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["figures"][0]["strategy"] == "hwp-slug"
    assert not (res.version_dir / "figures.partial.json").exists()


def test_rerun_makes_no_network_calls(fake, cache):
    run(fake, cache, f"{OLD_DOI}v3")
    n = len(fake.requests)
    again = run(fake, cache, f"{OLD_DOI}v3")
    assert again.from_cache and again.requests_made == 0 and len(fake.requests) == n


def test_rerun_latest_uses_cached_api_listing(fake, cache):
    first = run(fake, cache, OLD_DOI)
    assert first.manifest.version == 3  # latest
    n = len(fake.requests)
    again = run(fake, cache, OLD_DOI)
    assert again.from_cache and again.manifest.version == 3 and len(fake.requests) == n


def test_refresh_refetches(fake, cache):
    run(fake, cache, f"{OLD_DOI}v3")
    again = run(fake, cache, f"{OLD_DOI}v3", refresh=True)
    assert not again.from_cache and again.requests_made == 18


def test_version_flag_overrides_url_and_missing_version_lists_available(fake, cache):
    with pytest.raises(VersionNotFound, match=r"v7 does not exist\. Available: v1, v2, v3"):
        run(fake, cache, f"https://www.biorxiv.org/content/{OLD_DOI}v3", version=7)


def test_stale_cached_listing_is_rechecked_for_new_version(fake, cache):
    run(fake, cache, f"{OLD_DOI}v3")
    api_calls = lambda: sum("api.biorxiv.org" in u for u in fake.requests)
    before = api_calls()
    with pytest.raises(VersionNotFound):
        run(fake, cache, OLD_DOI, version=4)
    assert api_calls() == before + 1  # re-checked once instead of trusting the cache


# --- new 10.64898 paper: API jatsxml 404 -> full-text page -> real XML ---------------------
def test_new_prefix_paper_jats_fallback(fake, cache):
    res = run(fake, cache, NEW_DOI)
    m = res.manifest
    assert m.doi == NEW_DOI and m.version == 1
    assert m.jats_strategy == "fulltext-html-meta"
    assert m.jats_url == f"{NEW_BASE}.source.xml"
    assert m.api_entry["jatsxml"].endswith("/2026/10/20/2026.10.16.759679.source.xml")
    assert res.xml_path.read_bytes() == (NEW_DIR / "v1.source.xml").read_bytes()
    assert [f.parent_id for f in m.figures] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    assert all(f.status == "ok" and f.strategy == "hwp-slug" for f in m.figures)
    assert m.figures[0].source_url == f"{NEW_BASE}/F1.large.jpg"
    assert len(m.supplements) == 1
    # API, dead jatsxml, HTML page, XML, 6 images
    assert res.requests_made == 10
    assert (res.version_dir / "fulltext.html").exists()


def test_html_strategy_when_slug_urls_fail(fake, cache):
    # XML readable at the API's (10/20) path, but images only exist under 10/19, which the
    # full-text page links to. hwp-slug guesses 10/20 and misses; fulltext-html finds them.
    fake.add_xml("https://www.biorxiv.org/content/early/2026/10/20/2026.10.16.759679.source.xml", NEW_DIR / "v1.source.xml")
    fake.overrides.append(
        lambda req: httpx.Response(404, content=b"<html>nope</html>", headers={"content-type": "text/html"})
        if "/early/2026/10/20/2026.10.16.759679/" in req.url.path
        else None
    )
    res = run(fake, cache, NEW_DOI)
    m = res.manifest
    assert m.jats_strategy == "api-jatsxml"
    assert all(f.status == "ok" and f.strategy == "fulltext-html" for f in m.figures)
    assert m.figures[2].source_url == f"{NEW_BASE}/F3.large.jpg"
    assert sum(u.endswith("v1.full") for u in fake.requests) == 1  # page fetched once, reused


EQN_BASE = "https://www.biorxiv.org/content/biorxiv/early/2026/10/27/2026.10.22.760766"
EQN_EMBED = "https://www.biorxiv.org/sites/default/files/highwire/biorxiv/early/2026/10/27/2026.10.22.760766/embed"


def test_formula_images_via_embed_pattern_without_html_page(fake, cache):
    res = run(fake, cache, EQN_DOI)
    formulas = [f for f in res.manifest.figures if f.kind == "formula"]
    assert [(f.parent_id, f.graphic_id, f.href) for f in formulas] == [
        ("disp-formula-1", "graphic-6", "760766v1_ueqn1.gif"),
        ("disp-formula-2", "graphic-7", "760766v1_ueqn2.gif"),
    ]
    assert all(f.status == "ok" and f.strategy == "hwp-slug" for f in formulas)
    assert formulas[0].source_url == f"{EQN_EMBED}/graphic-6.gif"
    assert formulas[0].path == "figures/760766v1_ueqn1.png" and formulas[0].original_format == "GIF"

    # Both formula hrefs name a .gif, but the relative-href guess is spent only once.
    relative = [u for u in fake.requests if u.endswith(("_ueqn1.gif", "_ueqn2.gif"))]
    assert relative == ["https://www.biorxiv.org/content/biorxiv/early/2026/10/27/760766v1_ueqn1.gif"]
    assert not any(u.endswith(".full") for u in fake.requests)  # no full-text page download
    # API + XML (301 + 200) + 9 figures + 1 relative miss + 2 formulas
    assert res.requests_made == 15
    extended = [f for f in res.manifest.figures if f.label and f.label.startswith("Extended data")]
    assert [f.parent_id for f in extended] == ["F6", "F7", "F8", "F9"]


def test_embed_pattern_is_probe_once(fake, cache):
    fake.overrides.append(
        lambda req: httpx.Response(404, content=b"<html>no</html>", headers={"content-type": "text/html"})
        if "/embed/" in req.url.path
        else None
    )
    res = run(fake, cache, EQN_DOI)
    formulas = [f for f in res.manifest.figures if f.kind == "formula"]
    assert all(f.status == "missing" for f in formulas)
    assert sum("/embed/" in u for u in fake.requests) == 1  # second formula skips the dead pattern
    assert all(f.status == "ok" for f in res.manifest.figures if f.kind == "figure")


def test_missing_figures_recorded_and_run_continues(fake, cache, caplog):
    fake.serve_images = False
    res = run(fake, cache, f"{OLD_DOI}v3")
    assert all(f.status == "missing" and f.path is None for f in res.manifest.figures)
    assert "placeholder" in caplog.text
    assert (res.version_dir / "manifest.json").exists()


def test_resume_after_rate_limit_abort(fake, cache):
    image_hits = []

    def limiter(req):
        if req.url.path.endswith(".large.jpg"):
            image_hits.append(req.url.path)
            if len(image_hits) == 3:
                return httpx.Response(429, content=b"error code: 1015", headers={"retry-after": "9999"})
        return None

    fake.overrides.append(limiter)
    with pytest.raises(RateLimited):
        run(fake, cache, f"{OLD_DOI}v3")
    vdir = cache / "10.1101" / "2020.03.22.002386" / "v3"
    assert not (vdir / "manifest.json").exists()
    assert len(json.loads((vdir / "figures.partial.json").read_text(encoding="utf-8"))) == 2

    fake.overrides.clear()
    before = len(fake.requests)
    res = run(fake, cache, f"{OLD_DOI}v3")
    # Only the 13 remaining images: API listing and XML come from the cache.
    assert len(fake.requests) - before == 13
    assert all(f.status == "ok" for f in res.manifest.figures)


def test_supplements_opt_in_download(fake, cache):
    run(fake, cache, f"{OLD_DOI}v3")
    before = len(fake.requests)
    res = run(fake, cache, f"{OLD_DOI}v3", supplements=True)
    assert len(fake.requests) - before == 10  # just the supplement files
    assert all(s.status == "ok" for s in res.manifest.supplements)
    assert (res.version_dir / "supplements" / "002386_file07.pdf").read_bytes().startswith(b"%PDF")
    # Now complete including supplements: another run is a pure cache hit.
    assert run(fake, cache, f"{OLD_DOI}v3", supplements=True).requests_made == 0


def test_bare_doi_falls_back_to_medrxiv(fake, cache):
    doi = "10.1101/2020.03.22.20040758"
    entry = json.loads((NEW_DIR / "api.json").read_text(encoding="utf-8"))
    entry["collection"][0].update(
        doi=doi, server="medRxiv",
        jatsxml="https://www.medrxiv.org/content/early/2020/03/24/2020.03.22.20040758.source.xml",
    )
    fake.add(f"https://api.biorxiv.org/details/biorxiv/{doi}", body=b'{"messages":[{"status":"no posts found"}]}', ctype="application/json")
    fake.add(f"https://api.biorxiv.org/details/medrxiv/{doi}", body=json.dumps(entry).encode(), ctype="application/json")
    fake.add_xml(entry["collection"][0]["jatsxml"], NEW_DIR / "v1.source.xml")

    res = run(fake, cache, doi)
    assert res.manifest.server == "medrxiv" and res.resolved.server == "medrxiv"
    # Explicit server: no silent fallback.
    with pytest.raises(PreprintNotFound, match="not found on biorxiv"):
        run(fake, cache, doi, server="biorxiv", refresh=True)

    # ...not even when that version is cached (the cache is keyed by DOI, not server).
    n = len(fake.requests)
    assert run(fake, cache, f"{doi}v1", server="medrxiv").from_cache and len(fake.requests) == n
    with pytest.raises(PreprintNotFound, match="not found on biorxiv"):
        run(fake, cache, f"{doi}v1", server="biorxiv")
    with pytest.raises(PreprintNotFound, match="not found on biorxiv"):
        run(fake, cache, f"https://www.biorxiv.org/content/{doi}v1")  # server from the URL


def test_jats_unavailable_has_clear_message(fake, cache):
    entry = json.loads((NEW_DIR / "api.json").read_text(encoding="utf-8"))
    entry["collection"][0]["jatsxml"] = ""
    fake.add(f"https://api.biorxiv.org/details/biorxiv/{NEW_DOI}", body=json.dumps(entry).encode(), ctype="application/json")
    del fake.routes["https://www.biorxiv.org/content/10.64898/2026.10.16.759679v1.full"]
    with pytest.raises(JatsUnavailable, match="try again later"):
        run(fake, cache, NEW_DOI)


# --- CLI -------------------------------------------------------------------------------------
@pytest.fixture
def cli_env(fake, cache, monkeypatch):
    monkeypatch.setenv("RXIV2PDF_CACHE_DIR", str(cache))
    monkeypatch.setattr(cli, "make_client", fake.client)
    return CliRunner()


def test_cli_bad_input_exit_2(cli_env):
    result = cli_env.invoke(cli.app, ["not-a-doi"])
    assert result.exit_code == 2


def test_cli_not_found_exit_1(cli_env, fake):
    doi = "10.1101/2099.01.01.000001"
    for server in ("biorxiv", "medrxiv"):
        fake.add(f"https://api.biorxiv.org/details/{server}/{doi}", body=b'{"messages":[{"status":"no posts found"}]}', ctype="application/json")
    result = cli_env.invoke(cli.app, [doi])
    assert result.exit_code == 1


def test_cli_builds_pdf(cli_env, tmp_path):
    outdir = tmp_path / "out"
    result = cli_env.invoke(cli.app, [f"https://www.biorxiv.org/content/{OLD_DOI}v3.full", "-o", str(outdir)])
    assert result.exit_code == 0, result.output
    pdf = outdir / "2020.03.22.002386v3.pdf"
    assert pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 50_000
    assert sorted(p.name for p in outdir.iterdir()) == ["2020.03.22.002386v3.pdf"]  # no build litter
    assert "PDF" in result.stdout and "warning" not in result.output
    assert "figures 5/5, table images 10/10" in result.stdout and "hwp-slug: 15" in result.stdout


def test_cli_supplements_land_next_to_the_pdf(cli_env, tmp_path):
    outdir = tmp_path / "out"
    result = cli_env.invoke(cli.app, [f"{OLD_DOI}v3", "-o", str(outdir), "--supplements"])
    assert result.exit_code == 0, result.output
    supp = outdir / "2020.03.22.002386v3-supplements"
    assert len(list(supp.iterdir())) == 10 and (supp / "002386_file07.pdf").read_bytes().startswith(b"%PDF")
    assert result.stdout.count("SUPP") == 10 and "warning" not in result.output


def test_cli_append_supplements(cli_env, fake, tmp_path):
    import typst

    from rxiv2pdf.compile import pdf_page_count

    fake.supplement_bytes = typst.compile(b"Supplement page one #pagebreak() page two")  # a real 2-page PDF
    plain, appended = tmp_path / "plain", tmp_path / "appended"
    assert cli_env.invoke(cli.app, [f"{OLD_DOI}v3", "-o", str(plain)]).exit_code == 0
    result = cli_env.invoke(cli.app, [f"{OLD_DOI}v3", "-o", str(appended), "--append-supplements"])
    assert result.exit_code == 0, result.output
    # 3 of the 10 supplementary files are PDFs (2 pages each); the zip and 6 xlsx stay linked only.
    assert "Appended 3 supplementary file(s), 6 page(s)" in result.stdout
    assert "002386_file10.zip" in result.output and "002386_file11.xlsx" in result.output
    pdf = "2020.03.22.002386v3.pdf"
    assert pdf_page_count(appended / pdf) == pdf_page_count(plain / pdf) + 6
    assert sorted(p.name for p in appended.iterdir()) == [pdf]  # appending alone saves no separate files


def test_cli_paper_a4(cli_env, tmp_path):
    outdir = tmp_path / "out"
    result = cli_env.invoke(cli.app, [NEW_DOI, "-o", str(outdir), "--paper", "a4"])
    assert result.exit_code == 0, result.output
    assert b"/MediaBox[0 0 595.2756 841.8898]" in (outdir / "2026.10.16.759679v1.pdf").read_bytes()  # A4 in points


# --- batch mode and themes --------------------------------------------------------------------
def test_cli_batch_list_continues_past_failures(cli_env, tmp_path):
    listing = tmp_path / "dois.txt"
    listing.write_text(f"# my reading list\n{OLD_DOI}v3\n\nnot-a-doi\nhttps://doi.org/{NEW_DOI}\n", encoding="utf-8")
    outdir = tmp_path / "out"
    result = cli_env.invoke(cli.app, ["--list", str(listing), "-o", str(outdir)])
    assert result.exit_code == 1, result.output  # one item failed
    assert sorted(p.name for p in outdir.glob("*.pdf")) == ["2020.03.22.002386v3.pdf", "2026.10.16.759679v1.pdf"]
    assert "2/3 succeeded" in result.stdout and "not-a-doi" in result.stdout


def test_cli_batch_all_ok_exits_zero(cli_env, tmp_path):
    listing = tmp_path / "dois.txt"
    listing.write_text(f"{OLD_DOI}v3\n{NEW_DOI}\n", encoding="utf-8")
    result = cli_env.invoke(cli.app, ["--list", str(listing), "-o", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output and "2/2 succeeded" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        [],  # neither
        [OLD_DOI, "--list", "LIST"],  # both
        ["--list", "LIST", "--preprint-version", "2"],  # --preprint-version is per-preprint
    ],
)
def test_cli_argument_conflicts_exit_2(cli_env, tmp_path, args):
    listing = tmp_path / "dois.txt"
    listing.write_text(f"{OLD_DOI}\n", encoding="utf-8")
    args = [str(listing) if a == "LIST" else a for a in args]
    if not args:
        args = ["-o", str(tmp_path)]
    assert cli_env.invoke(cli.app, args).exit_code == 2


def unstyle(text: str) -> str:
    """Typer forces styled output when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is set, and
    the ANSI codes split option names; help text is checked without them."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_cli_no_arguments_shows_help(cli_env):
    result = cli_env.invoke(cli.app, [])
    text = unstyle(result.output)
    assert result.exit_code == 2 and "Usage:" in text and "--theme" in text


def test_cli_version_shows_the_tool_version(cli_env):
    from rxiv2pdf import __version__

    result = cli_env.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and result.stdout.strip() == f"rxiv2pdf {__version__}"


def test_cli_preprint_version_reaches_the_lookup(cli_env, tmp_path):
    result = cli_env.invoke(cli.app, [OLD_DOI, "--preprint-version", "9", "-o", str(tmp_path / "out")])
    assert result.exit_code == 1 and "v9 does not exist" in result.output and "v1, v2, v3" in result.output


def test_cli_help_shows_verbose_as_a_counter(cli_env):
    text = unstyle(cli_env.invoke(cli.app, ["--help"]).output)
    line = next(line for line in text.splitlines() if "--verbose" in line)
    assert "<int>" not in line and "[default: 0]" not in text


def test_summary_counts_requests_made_on_a_cache_hit(fake, cache, monkeypatch):
    import io

    from rich.console import Console

    from rxiv2pdf.fetch import doi_dir

    def summary(result) -> str:
        monkeypatch.setattr(cli, "out", Console(file=io.StringIO(), width=1000))
        cli._print_summary(result, verbose=0)
        return cli.out.file.getvalue()

    run(fake, cache, OLD_DOI)
    assert "cache hit, 0 network requests)" in summary(run(fake, cache, f"{OLD_DOI}v3"))
    (doi_dir(cache, OLD_DOI) / "api.json").unlink()  # the version list has to be looked up again
    again = run(fake, cache, OLD_DOI)
    assert again.from_cache and again.requests_made == 1
    assert "cache hit, 1 network request)" in summary(again)


def test_cli_theme_flag(cli_env, tmp_path, monkeypatch):
    seen = {}
    real = cli.build_pdf

    def spy(*args, **kwargs):
        seen["options"] = kwargs["options"]
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, "build_pdf", spy)
    result = cli_env.invoke(cli.app, [NEW_DOI, "-o", str(tmp_path / "out"), "--theme", "compact"])
    assert result.exit_code == 0, result.output
    assert seen["options"].theme == "compact"


# --- withdrawn preprints, --fallback-pdf, error messages -----------------------------------
def _withdrawn_api(fake):
    """OLD paper's real API JSON plus a v4 withdrawal notice (shape observed on bioRxiv)."""
    data = json.loads((OLD_DIR / "api.json").read_text(encoding="utf-8"))
    notice = dict(data["collection"][-1], version="4", type="withdrawn", date="2026-06-05",
                  title="WITHDRAWN: " + data["collection"][-1]["title"],
                  abstract="Withdrawal statementThe authors have withdrawn this manuscript because of an error.",
                  jatsxml="https://www.biorxiv.org/content/early/2026/06/05/2020.03.22.002386.source.xml")
    data["collection"].append(notice)
    fake.add(f"https://api.biorxiv.org/details/biorxiv/{OLD_DOI}", body=json.dumps(data).encode(), ctype="application/json")


def test_withdrawn_latest_renders_last_real_version_with_banner(fake, cache, tmp_path):
    _withdrawn_api(fake)
    res = run(fake, cache, OLD_DOI)
    m = res.manifest
    assert m.version == 3 and m.versions_available == [1, 2, 3, 4]
    assert m.withdrawn == {"version": 4, "date": "2026-06-05",
                           "statement": "The authors have withdrawn this manuscript because of an error."}
    from rxiv2pdf.jats import parse_jats
    from rxiv2pdf.render_typst import render

    article = parse_jats(res.xml_path.read_bytes(), meta=m)
    source, _ = render(article, assets={})
    assert 'withdrawn: (date: "2026-06-05", version: 4, statement: [The authors have withdrawn' in source


def test_withdrawn_explicit_version_is_honoured(fake, cache):
    _withdrawn_api(fake)
    with pytest.raises(JatsUnavailable):  # v4's JATS is not routed in the fake
        run(fake, cache, f"{OLD_DOI}v4")
    assert any("/2026/06/05/" in u for u in fake.requests)  # it really tried v4, not a fallback


def test_fallback_pdf_when_no_jats(cli_env, fake, tmp_path):
    entry = json.loads((NEW_DIR / "api.json").read_text(encoding="utf-8"))
    entry["collection"][0]["jatsxml"] = ""
    fake.add(f"https://api.biorxiv.org/details/biorxiv/{NEW_DOI}", body=json.dumps(entry).encode(), ctype="application/json")
    fake.add(f"{NEW_BASE}.full.pdf", body=b"%PDF-1.7 original", ctype="application/pdf")
    del fake.routes[f"{NEW_BASE}.source.xml"]  # no JATS anywhere
    outdir = tmp_path / "out"

    plain = cli_env.invoke(cli.app, [NEW_DOI, "-o", str(outdir)])
    assert plain.exit_code == 1 and "--fallback-pdf" in plain.output

    result = cli_env.invoke(cli.app, [NEW_DOI, "-o", str(outdir), "--fallback-pdf"])
    assert result.exit_code == 0, result.output
    assert (outdir / "2026.10.16.759679v1.pdf").read_bytes() == b"%PDF-1.7 original"
    assert "original PDF" in result.output


def test_fallback_pdf_reports_an_html_page_instead_of_a_pdf(cli_env, fake, tmp_path):
    entry = json.loads((NEW_DIR / "api.json").read_text(encoding="utf-8"))
    entry["collection"][0]["jatsxml"] = ""
    fake.add(f"https://api.biorxiv.org/details/biorxiv/{NEW_DOI}", body=json.dumps(entry).encode(), ctype="application/json")
    fake.add(f"{NEW_BASE}.full.pdf", body=b"<!DOCTYPE html><html><body>Article</body></html>", ctype="text/html")
    del fake.routes[f"{NEW_BASE}.source.xml"]
    result = cli_env.invoke(cli.app, [NEW_DOI, "-o", str(tmp_path / "out"), "--fallback-pdf"])
    assert result.exit_code == 1 and "did not return a PDF" in result.output, result.output


def test_typst_failure_keeps_debug_source(cli_env, tmp_path, monkeypatch):
    from rxiv2pdf import compile as compile_mod

    monkeypatch.setattr(compile_mod, "render", lambda *a, **k: ("#let broken = (", []))
    outdir = tmp_path / "out"
    result = cli_env.invoke(cli.app, [f"{OLD_DOI}v3", "-o", str(outdir)])
    assert result.exit_code == 1
    assert "bug in rxiv2pdf" in result.output and "typst-debug" in result.output
    assert (outdir / "2020.03.22.002386v3-typst-debug" / "2020.03.22.002386v3.typ").read_text(encoding="utf-8") == "#let broken = ("


def test_internal_error_names_preprint_and_asks_for_report(cli_env, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise KeyError("surprise")

    monkeypatch.setattr(cli, "parse_jats", boom)
    result = cli_env.invoke(cli.app, [f"{OLD_DOI}v3", "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "10.1101/2020.03.22.002386 v3" in result.output and "bug in rxiv2pdf" in result.output
    assert "Traceback" not in result.output  # only with -v


def test_uncreatable_outdir_exit_2(cli_env, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = cli_env.invoke(cli.app, [OLD_DOI, "-o", str(blocker / "sub")])
    assert result.exit_code == 2 and "cannot create the output directory" in result.output


def test_cli_chemrxiv_exit_2_with_reason(cli_env):
    result = cli_env.invoke(cli.app, ["https://doi.org/10.26434/chemrxiv.15008199/v1"])
    assert result.exit_code == 2 and "ChemRxiv publishes only the authors' PDFs" in " ".join(result.output.split())


def test_default_cache_root_per_platform(monkeypatch):
    from types import SimpleNamespace

    from rxiv2pdf import fetch

    def root(name, **env):
        monkeypatch.setattr(fetch, "os", SimpleNamespace(name=name, environ=env))
        return fetch.default_cache_root()

    assert root("nt", LOCALAPPDATA="C:/Users/me/AppData/Local") == Path("C:/Users/me/AppData/Local/rxiv2pdf/Cache")
    assert root("posix") == Path.home() / ".cache" / "rxiv2pdf"
    assert root("nt", LOCALAPPDATA="C:/x", XDG_CACHE_HOME="/xdg") == Path("/xdg/rxiv2pdf")  # explicit choices win
    assert root("posix", RXIV2PDF_CACHE_DIR="/mine", XDG_CACHE_HOME="/xdg") == Path("/mine")
