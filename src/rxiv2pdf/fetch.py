"""Stage 2 - Fetch: API metadata, JATS XML, figures and (optionally) supplements, cached.

Ground truth from probing the live service (Oct 2026), which the code below follows:

API  ``GET https://api.biorxiv.org/details/{server}/{doi}`` -> 200 JSON
     ``{"messages": [{"status": "ok"}], "collection": [{version: "1", date, jatsxml, ...}, ...]}``.
     Unknown DOI -> still 200, ``status: "no posts found"``, empty collection.

JATS The API's ``jatsxml`` (``/content/early/Y/M/D/{suffix}.source.xml``) 301-redirects to
     ``/content/{server}/early/Y/M/D/{suffix}.source.xml``. For some recent posts it 404s
     because the date in the path is one day off from the real content path. The real
     path is in the full-text HTML page's ``citation_pdf_url`` meta tag
     (``...{suffix}.full.pdf``), so that is the fallback.

Figs ``<graphic xlink:href="002386v3_fig1">`` hrefs have NO extension and do not resolve
     relative to the XML URL (404). Every ``fig``/``table-wrap`` and ``graphic`` carries
     ``hwp:id`` (``F1``/``T1``, ``graphic-5``) and images live under the content base
     ``{xml url minus .source.xml}/``:  ``F1.large.jpg`` for figures,
     ``T1/graphic-5.large.jpg`` for (often multi-image) tables.

Supp ``<supplementary-material hwp:id="DC1"><media hwp:id="media-1" xlink:href="supplements/x.pdf">``
     -> ``{base}/DC1/embed/media-1.pdf``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html
from PIL import Image

from . import __version__
from .images import normalize, sniff_ext
from .net import Client, FetchError, NotFound
from .resolve import SERVERS, Resolved

log = logging.getLogger(__name__)

API_BASE = "https://api.biorxiv.org"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
HWP_ID = "{http://schema.highwire.org/Journal}id"
MANIFEST_SCHEMA = 1

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp"}
_ASSET_PARENTS = (
    "fig", "table-wrap", "disp-formula", "inline-formula", "supplementary-material",
    "boxed-text", "chem-struct-wrap",
)
_KIND = {"fig": "figure", "table-wrap": "table", "disp-formula": "formula", "inline-formula": "formula"}


# ---------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------
class PreprintNotFound(FetchError):
    pass


class VersionNotFound(FetchError):
    pass


class JatsUnavailable(FetchError):
    pass


# ---------------------------------------------------------------------------------------
# Typed stage output
# ---------------------------------------------------------------------------------------
@dataclass
class FigureAsset:
    href: str
    kind: str  # figure | table | formula | other
    parent_tag: str | None
    parent_id: str | None  # hwp:id, e.g. "F1", "T1"
    parent_xml_id: str | None  # @id, e.g. "fig1"
    graphic_id: str | None  # hwp:id, e.g. "graphic-5"
    label: str | None
    status: str  # ok | missing
    path: str | None = None  # relative to the version dir
    source_url: str | None = None
    strategy: str | None = None
    format: str | None = None
    original_format: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class SupplementAsset:
    id: str | None  # hwp:id, e.g. "DC1"
    media_id: str | None  # hwp:id, e.g. "media-1"
    label: str | None
    href: str
    url: str | None
    path: str | None = None  # set once downloaded (--supplements)
    status: str = "listed"  # listed | ok | missing


@dataclass
class Manifest:
    schema: int
    tool_version: str
    server: str
    doi: str
    version: int
    versions_available: list[int]
    slug: str
    title: str
    date: str
    category: str
    license: str
    type: str
    published: str
    jats_url: str  # final URL the XML was read from
    jats_strategy: str
    content_base: str | None
    fetched_at: str
    figures: list[FigureAsset] = field(default_factory=list)
    supplements: list[SupplementAsset] = field(default_factory=list)
    api_entry: dict[str, Any] = field(default_factory=dict)
    withdrawn: dict[str, Any] | None = None  # {"version", "date", "statement"} if withdrawn

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        data = dict(data)
        data["figures"] = [FigureAsset(**f) for f in data.get("figures", [])]
        data["supplements"] = [SupplementAsset(**s) for s in data.get("supplements", [])]
        return cls(**data)


@dataclass
class FetchResult:
    resolved: Resolved
    version_dir: Path
    xml_path: Path
    manifest: Manifest
    from_cache: bool
    requests_made: int


# ---------------------------------------------------------------------------------------
# Cache layout:  {root}/{doi}/api.json            (API details, all versions)
#                {root}/{doi}/v{N}/article.xml
#                {root}/{doi}/v{N}/manifest.json  (written last = "complete")
#                {root}/{doi}/v{N}/figures/, supplements/, fulltext.html
# ---------------------------------------------------------------------------------------
def default_cache_root() -> Path:
    """RXIV2PDF_CACHE_DIR, else $XDG_CACHE_HOME/rxiv2pdf, else the platform's cache folder:
    %LOCALAPPDATA%\\rxiv2pdf\\Cache on Windows, ~/.cache/rxiv2pdf elsewhere."""
    if env := os.environ.get("RXIV2PDF_CACHE_DIR"):
        return Path(env).expanduser()
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg).expanduser() / "rxiv2pdf"
    if os.name == "nt" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "rxiv2pdf" / "Cache"
    return Path.home() / ".cache" / "rxiv2pdf"


def doi_dir(cache_root: Path, doi: str) -> Path:
    prefix, suffix = doi.split("/", 1)
    return cache_root / prefix / suffix


def _write_atomic(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    tmp.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------
def _collection(payload: dict[str, Any]) -> list[dict[str, Any]]:
    coll = payload.get("collection") or []
    return [e for e in coll if isinstance(e, dict) and str(e.get("version", "")).isdigit()]


def _api_details(client: Client, server: str, doi: str) -> dict[str, Any]:
    resp = client.get(f"{API_BASE}/details/{server}/{doi}", expect="json")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"bioRxiv API returned invalid JSON for {doi}: {exc}") from exc


def _lookup(
    client: Client, resolved: Resolved, ddir: Path, refresh: bool
) -> tuple[str, list[dict[str, Any]], bool]:
    """Return (server, collection, from_cache). Uses the cached api.json unless ``refresh``."""
    cache = ddir / "api.json"
    if not refresh and cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if resolved.server_source == "default" or cached.get("server") == resolved.server:
            coll = _collection(cached.get("response", {}))
            if coll:
                log.debug("API details from cache: %s", cache)
                return cached["server"], coll, True

    servers = [resolved.server]
    if resolved.server_source == "default":  # bare DOI: medRxiv DOIs look the same
        servers += [s for s in SERVERS if s != resolved.server]

    for server in servers:
        payload = _api_details(client, server, resolved.doi)
        coll = _collection(payload)
        if coll:
            _write_atomic(cache, json.dumps({"server": server, "fetched_at": _now(), "response": payload}, indent=2))
            return server, coll, False
        status = (payload.get("messages") or [{}])[0].get("status", "?")
        log.info("Not on %s (API status: %s)", server, status)

    tried = " or ".join(servers)
    raise PreprintNotFound(
        f"{resolved.doi} was not found on {tried}. Check the DOI"
        + (", or pass --server medrxiv/biorxiv." if resolved.server_source != "default" else ".")
    )


def _is_withdrawn(entry: dict[str, Any]) -> bool:
    # Observed: a withdrawal is a new version with type "withdrawn", title "WITHDRAWN: ...",
    # the statement as its abstract, and JATS with no body.
    return str(entry.get("type", "")).lower() == "withdrawn" or str(entry.get("title", "")).upper().startswith("WITHDRAWN")


def _withdrawal(collection: list[dict[str, Any]]) -> dict[str, Any] | None:
    notices = sorted((e for e in collection if _is_withdrawn(e)), key=lambda e: int(e["version"]))
    if not notices:
        return None
    e = notices[-1]
    statement = re.sub(r"^\s*withdrawal statement[:.]?\s*", "", str(e.get("abstract", "")), flags=re.IGNORECASE).strip()
    return {"version": int(e["version"]), "date": str(e.get("date", "")), "statement": statement}


def _pick_version(collection: list[dict[str, Any]], requested: int | None) -> tuple[dict[str, Any], list[int]]:
    by_version = {int(e["version"]): e for e in collection}
    available = sorted(by_version)
    if requested is None:
        # "Latest" skips a withdrawal notice (no body) in favour of the last real version.
        real = [v for v in available if not _is_withdrawn(by_version[v])]
        return by_version[real[-1] if real else available[-1]], available
    if requested not in by_version:
        listed = ", ".join(f"v{v}" for v in available)
        raise VersionNotFound(f"Version v{requested} does not exist. Available: {listed}")
    return by_version[requested], available


# ---------------------------------------------------------------------------------------
# Full-text HTML page (fetched lazily, at most once, and cached)
# ---------------------------------------------------------------------------------------
class _FulltextPage:
    def __init__(self, client: Client, server: str, doi: str, version: int, vdir: Path, refresh: bool):
        self.url = f"https://www.{server}.org/content/{doi}v{version}.full"
        self._client = client
        self._path = vdir / "fulltext.html"
        self._refresh = refresh
        self._doc: Any = None
        self._failed = False
        self._images: dict[str, str] | None = None

    def doc(self) -> Any:
        if self._doc is None and not self._failed:
            try:
                if self._path.exists() and not self._refresh:
                    raw = self._path.read_bytes()
                else:
                    raw = self._client.get(self.url, expect="html").content
                    _write_atomic(self._path, raw)
                self._doc = lxml_html.fromstring(raw, base_url=self.url)
            except NotFound:
                log.warning("Full-text HTML page not found: %s", self.url)
                self._failed = True
        return self._doc

    def meta(self, name: str) -> str | None:
        doc = self.doc()
        if doc is None:
            return None
        found = doc.xpath("//meta[@name=$n]/@content", n=name)
        return str(found[0]).strip() if found else None

    def images(self) -> dict[str, str]:
        """Map 'F1.large.jpg' / 'T1/graphic-5.large.jpg' (path after the slug dir) -> absolute URL.

        Keys keep page order (dicts are ordered), which the order-based fallback relies on.
        """
        if self._images is None:
            self._images = {}
            doc = self.doc()
            if doc is not None:
                for raw in doc.xpath("//a/@href | //img/@src"):
                    url = urljoin(self.url, str(raw))
                    parts = urlsplit(url)
                    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                    m = re.search(r"/early/\d{4}/\d{2}/\d{2}/[^/]+/(.+\.(?:jpe?g|png|gif|tiff?))$", parts.path, re.IGNORECASE)
                    if m and m.group(1) not in self._images:
                        self._images[m.group(1)] = clean
        return self._images


# ---------------------------------------------------------------------------------------
# JATS
# ---------------------------------------------------------------------------------------
def _parse_xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=False)
    return etree.fromstring(data, parser=parser)


def _content_base(xml_url: str) -> str | None:
    """``.../2020.03.22.002386.source.xml`` -> ``.../2020.03.22.002386`` (no trailing slash)."""
    path = urlsplit(xml_url).path
    if not path.endswith(".source.xml"):
        return None
    return xml_url.split("?", 1)[0][: -len(".source.xml")]


def _download_jats(
    client: Client, entry: dict[str, Any], page: _FulltextPage, doi: str, version: int
) -> tuple[bytes, str, str]:
    """Return (xml bytes, final URL, strategy name)."""
    attempts: list[str] = []

    api_url = (entry.get("jatsxml") or "").strip()
    if api_url:
        try:
            resp = client.get(api_url, expect="xml")
            return resp.content, str(resp.url), "api-jatsxml"
        except NotFound:
            attempts.append(f"API jatsxml URL 404: {api_url}")
            log.info("API jatsxml URL is 404 (known date mismatch); trying the full-text page")
    else:
        attempts.append("API entry has no jatsxml URL")

    pdf_url = page.meta("citation_pdf_url")
    if pdf_url and pdf_url.endswith(".full.pdf"):
        xml_url = pdf_url[: -len(".full.pdf")] + ".source.xml"
        if xml_url != api_url:
            try:
                resp = client.get(xml_url, expect="xml")
                return resp.content, str(resp.url), "fulltext-html-meta"
            except NotFound:
                attempts.append(f"citation_pdf_url-derived XML 404: {xml_url}")
    else:
        attempts.append("full-text page has no citation_pdf_url")

    raise JatsUnavailable(
        f"No JATS XML is available for {doi} v{version} yet. bioRxiv/medRxiv usually publish "
        "the XML a few days after a version is posted, so try again later, or pass --fallback-pdf "
        "to get the original PDF instead. Tried: " + "; ".join(attempts)
    )


# ---------------------------------------------------------------------------------------
# Figure discovery + strategies
# ---------------------------------------------------------------------------------------
@dataclass
class _GraphicRef:
    href: str
    graphic_id: str | None
    parent_tag: str | None
    parent_id: str | None
    parent_xml_id: str | None
    label: str | None
    graphics_in_parent: int


def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _find_graphics(root: etree._Element) -> list[_GraphicRef]:
    refs: list[_GraphicRef] = []
    seen: set[str] = set()
    for el in root.iter("graphic", "inline-graphic"):
        href = (el.get(XLINK_HREF) or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        parent = next((a for a in el.iterancestors() if _local(a.tag) in _ASSET_PARENTS), None)
        label = None
        siblings = 1
        if parent is not None:
            label_el = parent.find("label")
            label = " ".join("".join(label_el.itertext()).split()) if label_el is not None else None
            siblings = sum(1 for _ in parent.iter("graphic"))
        refs.append(
            _GraphicRef(
                href=href,
                graphic_id=el.get(HWP_ID) or el.get("id"),
                parent_tag=_local(parent.tag) if parent is not None else None,
                parent_id=(parent.get(HWP_ID) or parent.get("id")) if parent is not None else None,
                parent_xml_id=parent.get("id") if parent is not None else None,
                label=label,
                graphics_in_parent=siblings,
            )
        )
    return refs


def _find_supplements(root: etree._Element, base: str | None) -> list[SupplementAsset]:
    out: list[SupplementAsset] = []
    for sm in root.iter("supplementary-material"):
        label_el = sm.find("label")
        label = " ".join("".join(label_el.itertext()).split()) if label_el is not None else None
        sm_id = sm.get(HWP_ID) or sm.get("id")
        for media in sm.iter("media"):
            href = (media.get(XLINK_HREF) or "").strip()
            if not href:
                continue
            media_id = media.get(HWP_ID) or media.get("id")
            url = None
            if base and sm_id and media_id:
                url = f"{base}/{sm_id}/embed/{media_id}{PurePosixPath(href).suffix}"
            out.append(SupplementAsset(id=sm_id, media_id=media_id, label=label, href=href, url=url))
    return out


# URL patterns that are only guesses for a given paper: if the first attempt 404s, the
# pattern is dropped for the rest of that paper, so a wrong guess costs one request, not N.
_PROBE_ONCE = {"xml-relative", "hwp-embed"}

Candidate = tuple[str, str]  # (url, pattern name)


class _StrategyContext:
    def __init__(self, xml_url: str, base: str | None, page: _FulltextPage, graphics: list[_GraphicRef]):
        self.xml_url = xml_url
        self.base = base
        self.embed_base = _embed_base(base)
        self.page = page
        self.fig_order = [g.href for g in graphics if g.parent_tag == "fig"]
        self.pattern_hits: set[str] = set()
        self.dead_patterns: set[str] = set()


def _embed_base(base: str | None) -> str | None:
    """Content base -> where HighWire serves inline/formula images (verified live, Oct 2026):

    ``https://www.biorxiv.org/content/biorxiv/early/2026/10/27/2026.10.22.760766``
    ``https://www.biorxiv.org/sites/default/files/highwire/biorxiv/early/2026/10/27/2026.10.22.760766``
    """
    if not base:
        return None
    parts = urlsplit(base)
    if not parts.path.startswith("/content/"):
        return None
    path = "/sites/default/files/highwire/" + parts.path[len("/content/") :]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _cand_xml_relative(g: _GraphicRef, ctx: _StrategyContext) -> list[Candidate]:
    # Strategy 1. Never seen to work on bioRxiv: figure hrefs have no extension, and
    # formula hrefs (``760766v1_ueqn1.gif``) do but still 404. Only tried for hrefs that
    # name an image file, and dropped after the first miss (probe-once).
    if PurePosixPath(g.href).suffix.lower() in _IMAGE_EXTS:
        return [(urljoin(ctx.xml_url, g.href), "xml-relative")]
    return []


def _cand_hwp_slug(g: _GraphicRef, ctx: _StrategyContext) -> list[Candidate]:
    # Derived from probing: images are keyed by hwp:id under the XML's content base.
    #   fig        {base}/F1.large.jpg                    (verified)
    #   table-wrap {base}/T1/graphic-5.large.jpg          (verified, multi-image tables)
    #   other      {embed_base}/embed/graphic-6.gif       (verified for disp-formula; probe-once
    #                                                      for inline formulas/graphics)
    if g.parent_tag in ("fig", "table-wrap"):
        if not ctx.base or not g.parent_id:
            return []
        single = (f"{ctx.base}/{g.parent_id}.large.jpg", "hwp-figure")
        nested = (f"{ctx.base}/{g.parent_id}/{g.graphic_id}.large.jpg", "hwp-figure") if g.graphic_id else None
        if g.parent_tag == "fig" and g.graphics_in_parent == 1:
            return [c for c in (single, nested) if c]
        return [c for c in (nested, single if g.graphics_in_parent == 1 else None) if c]
    if ctx.embed_base and g.graphic_id:
        suffix = PurePosixPath(g.href).suffix.lower()
        ext = suffix if suffix in _IMAGE_EXTS else ".gif"
        return [(f"{ctx.embed_base}/embed/{g.graphic_id}{ext}", "hwp-embed")]
    return []


def _cand_fulltext_html(g: _GraphicRef, ctx: _StrategyContext) -> list[Candidate]:
    # Strategy 2: image links in the full-text page, matched by hwp:id, then by order.
    images = ctx.page.images()
    if not images:
        return []
    keys = []
    if g.parent_id and g.graphic_id:
        keys.append(f"{g.parent_id}/{g.graphic_id}.large.jpg")
    if g.parent_id and g.graphics_in_parent == 1:
        keys.append(f"{g.parent_id}.large.jpg")
    for key in keys:
        if key in images:
            return [(images[key], "html")]
    if g.graphic_id:  # e.g. formula images: any page image whose file stem is the graphic's hwp:id
        hits = [u for k, u in images.items() if PurePosixPath(k).name.split(".")[0] == g.graphic_id]
        hits.sort(key=lambda u: ".large." not in u)
        if hits:
            return [(hits[0], "html")]
    if g.parent_tag == "fig" and g.href in ctx.fig_order:
        fig_links = [u for k, u in images.items() if re.fullmatch(r"F\d+\.large\.jpg", k)]
        idx = ctx.fig_order.index(g.href)
        if len(fig_links) == len(ctx.fig_order):
            return [(fig_links[idx], "html")]
    return []


STRATEGIES = (
    ("xml-relative", _cand_xml_relative),
    ("hwp-slug", _cand_hwp_slug),
    ("fulltext-html", _cand_fulltext_html),
)


def _safe_stem(href: str) -> str:
    name = PurePosixPath(href).name
    stem = name.rsplit(".", 1)[0] if PurePosixPath(name).suffix.lower() in _IMAGE_EXTS else name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem) or "graphic"


def _fetch_graphic(client: Client, g: _GraphicRef, ctx: _StrategyContext, vdir: Path) -> FigureAsset:
    asset = FigureAsset(
        href=g.href,
        kind=_KIND.get(g.parent_tag or "", "other"),
        parent_tag=g.parent_tag,
        parent_id=g.parent_id,
        parent_xml_id=g.parent_xml_id,
        graphic_id=g.graphic_id,
        label=g.label,
        status="missing",
    )
    tried: set[str] = set()
    for name, candidates in STRATEGIES:
        for url, pattern in candidates(g, ctx):
            if url in tried or pattern in ctx.dead_patterns:
                continue
            tried.add(url)
            try:
                resp = client.get(url, expect="image")
            except NotFound:
                log.debug("  %s: 404 %s", name, url)
                if pattern in _PROBE_ONCE and pattern not in ctx.pattern_hits:
                    ctx.dead_patterns.add(pattern)
                    log.debug("  %s: first attempt missed; not trying it again for this paper", pattern)
                continue
            ctx.pattern_hits.add(pattern)
            raw_path = vdir / "figures" / (_safe_stem(g.href) + sniff_ext(resp.content[:16], resp.headers.get("content-type")))
            _write_atomic(raw_path, resp.content)
            try:
                info = normalize(raw_path)
            except (OSError, Image.DecompressionBombError) as exc:
                log.warning("  %s: %s is not a usable image (%s)", name, url, exc)
                raw_path.unlink(missing_ok=True)
                continue
            asset.status = "ok"
            asset.path = info.path.relative_to(vdir).as_posix()
            asset.source_url = str(resp.url)
            asset.strategy = name
            asset.format = info.format
            asset.original_format = info.original_format
            asset.width, asset.height = info.width, info.height
            return asset
    return asset


# ---------------------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------------------
def fetch(
    resolved: Resolved,
    *,
    client: Client,
    cache_root: Path | None = None,
    refresh: bool = False,
    supplements: bool = False,
) -> FetchResult:
    """Run the Fetch stage. Re-runs make no network calls unless ``refresh`` (or new work is asked for)."""
    cache_root = cache_root or default_cache_root()
    ddir = doi_dir(cache_root, resolved.doi)
    start_requests = client.request_count

    # Fast path: explicit version already complete in the cache -> no network at all. The cache
    # is keyed by DOI, so a server named by --server or the URL must match the cached one;
    # otherwise the lookup below asks that server, as it would with an empty cache.
    if resolved.version is not None and not refresh:
        cached = _load_complete(ddir / f"v{resolved.version}", supplements)
        if cached and (resolved.server_source == "default" or cached.server == resolved.server):
            return _result(resolved, ddir / f"v{resolved.version}", cached, True, 0)

    server, collection, api_from_cache = _lookup(client, resolved, ddir, refresh)
    try:
        entry, available = _pick_version(collection, resolved.version)
    except VersionNotFound:
        if not api_from_cache:
            raise
        log.info("v%s not in cached API listing; re-checking the API", resolved.version)
        server, collection, _ = _lookup(client, resolved, ddir, refresh=True)
        entry, available = _pick_version(collection, resolved.version)
    version = int(entry["version"])
    vdir = ddir / f"v{version}"
    if server != resolved.server:
        log.info("Found on %s (not %s)", server, resolved.server)

    if not refresh:
        cached = _load_complete(vdir, supplements)
        if cached:
            return _result(resolved, vdir, cached, True, client.request_count - start_requests)
        manifest = _load_manifest(vdir)
        if manifest and supplements:  # complete except for supplement downloads
            _download_supplements(client, manifest, vdir)
            _write_atomic(vdir / "manifest.json", manifest.to_json())
            return _result(resolved, vdir, manifest, False, client.request_count - start_requests)
    elif vdir.exists():
        shutil.rmtree(vdir)

    vdir.mkdir(parents=True, exist_ok=True)
    page = _FulltextPage(client, server, resolved.doi, version, vdir, refresh)

    # JATS: reuse a previously downloaded file (interrupted run) if it parses.
    xml_path = vdir / "article.xml"
    xml_meta_path = vdir / "jats_source.json"
    xml_bytes: bytes | None = None
    jats_url = jats_strategy = ""
    if xml_path.exists() and xml_meta_path.exists():
        xml_bytes = xml_path.read_bytes()
        meta = json.loads(xml_meta_path.read_text(encoding="utf-8"))
        jats_url, jats_strategy = meta["url"], meta["strategy"]
    if xml_bytes is None:
        xml_bytes, jats_url, jats_strategy = _download_jats(client, entry, page, resolved.doi, version)
        _write_atomic(xml_path, xml_bytes)
        _write_atomic(xml_meta_path, json.dumps({"url": jats_url, "strategy": jats_strategy}))
    log.info("JATS XML: %s KB via %s", len(xml_bytes) // 1024, jats_strategy)

    try:
        root = _parse_xml(xml_bytes)
    except etree.XMLSyntaxError as exc:
        xml_path.unlink(missing_ok=True)
        raise FetchError(f"Downloaded JATS for {resolved.doi} v{version} is not valid XML: {exc}") from exc

    xml_doi = root.findtext(".//article-meta/article-id[@pub-id-type='doi']")
    if xml_doi and xml_doi.strip().lower() != resolved.doi.lower():
        log.warning("JATS declares DOI %s but %s was requested", xml_doi.strip(), resolved.doi)

    base = _content_base(jats_url)
    graphics = _find_graphics(root)
    ctx = _StrategyContext(jats_url, base, page, graphics)

    # Resume support: figures finished by an interrupted run are kept in a partial list.
    partial_path = vdir / "figures.partial.json"
    done: dict[str, FigureAsset] = {}
    if partial_path.exists():
        for item in json.loads(partial_path.read_text(encoding="utf-8")):
            asset = FigureAsset(**item)
            if asset.status == "ok" and asset.path and (vdir / asset.path).exists():
                done[asset.href] = asset

    figures: list[FigureAsset] = []
    for i, g in enumerate(graphics, 1):
        what = g.label or g.parent_id or g.href
        if g.href in done:
            figures.append(done[g.href])
            log.debug("[%d/%d] %s: cached", i, len(graphics), what)
            continue
        asset = _fetch_graphic(client, g, ctx, vdir)
        figures.append(asset)
        if asset.status == "ok":
            log.info("[%d/%d] %s <- %s (%dx%d)", i, len(graphics), what, asset.strategy, asset.width, asset.height)
        else:
            log.warning("[%d/%d] %s: no strategy found an image; the PDF will show a placeholder", i, len(graphics), what)
        _write_atomic(partial_path, json.dumps([asdict(f) for f in figures], indent=2))

    manifest = Manifest(
        schema=MANIFEST_SCHEMA,
        tool_version=__version__,
        server=server,
        doi=resolved.doi,
        version=version,
        versions_available=available,
        slug=f"{resolved.suffix}v{version}",
        title=str(entry.get("title", "")),
        date=str(entry.get("date", "")),
        category=str(entry.get("category", "")),
        license=str(entry.get("license", "")),
        type=str(entry.get("type", "")),
        published=str(entry.get("published", "")),
        jats_url=jats_url,
        jats_strategy=jats_strategy,
        content_base=base,
        fetched_at=_now(),
        figures=figures,
        supplements=_find_supplements(root, base),
        api_entry=entry,
        withdrawn=_withdrawal(collection),
    )
    if manifest.withdrawn:
        log.warning("This preprint was withdrawn on %s (v%s); rendering v%s with a withdrawal banner",
                    manifest.withdrawn["date"], manifest.withdrawn["version"], version)
    if supplements:
        _download_supplements(client, manifest, vdir)

    _write_atomic(vdir / "manifest.json", manifest.to_json())  # written last: marks the version complete
    partial_path.unlink(missing_ok=True)
    return _result(resolved, vdir, manifest, False, client.request_count - start_requests)


def fetch_original_pdf(resolved: Resolved, *, client: Client, cache_root: Path | None = None) -> tuple[Path, str]:
    """--fallback-pdf: download the server's own PDF (for when no JATS exists yet).

    Returns (cached PDF path, slug). The URL comes from the article page's
    citation_pdf_url, which (unlike the API's jatsxml path) has the right date.
    """
    cache_root = cache_root or default_cache_root()
    ddir = doi_dir(cache_root, resolved.doi)
    server, collection, _ = _lookup(client, resolved, ddir, refresh=False)
    entry, _ = _pick_version(collection, resolved.version)
    version = int(entry["version"])
    vdir = ddir / f"v{version}"
    dest = vdir / "original.pdf"
    if not dest.exists():
        page = _FulltextPage(client, server, resolved.doi, version, vdir, refresh=False)
        urls = [u for u in (page.meta("citation_pdf_url"),) if u]
        jats = str(entry.get("jatsxml") or "")
        if jats.endswith(".source.xml"):
            urls.append(jats[: -len(".source.xml")] + ".full.pdf")
        for url in urls:
            try:
                client.download(url, dest, expect="any")
                break
            except NotFound:
                continue
        else:
            raise FetchError(f"No PDF found for {resolved.doi} v{version} either (tried: {', '.join(urls) or 'nothing to try'})")
        if dest.read_bytes()[:5] != b"%PDF-":
            dest.unlink()
            raise FetchError(f"The server did not return a PDF for {resolved.doi} v{version}")
    return dest, f"{resolved.suffix}v{version}"


def _result(resolved: Resolved, vdir: Path, manifest: Manifest, from_cache: bool, requests: int) -> FetchResult:
    final = Resolved(manifest.server, manifest.doi, manifest.version, resolved.server_source)  # type: ignore[arg-type]
    return FetchResult(final, vdir, vdir / "article.xml", manifest, from_cache, requests)


def _load_manifest(vdir: Path) -> Manifest | None:
    path = vdir / "manifest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != MANIFEST_SCHEMA:
            return None
        manifest = Manifest.from_dict(data)
    except (ValueError, TypeError, KeyError):
        log.warning("Ignoring unreadable cache manifest %s", path)
        return None
    if not (vdir / "article.xml").exists():
        return None
    if any(f.status == "ok" and not (vdir / f.path).exists() for f in manifest.figures if f.path):
        return None
    return manifest


def _load_complete(vdir: Path, supplements: bool) -> Manifest | None:
    manifest = _load_manifest(vdir)
    if manifest is None:
        return None
    if supplements and any(s.status == "listed" and s.url for s in manifest.supplements):
        return None
    return manifest


def _download_supplements(client: Client, manifest: Manifest, vdir: Path) -> None:
    for s in manifest.supplements:
        if s.status == "ok" and s.path and (vdir / s.path).exists():
            continue
        if not s.url:
            s.status = "missing"
            continue
        dest = vdir / "supplements" / re.sub(r"[^A-Za-z0-9._-]+", "_", PurePosixPath(s.href).name)
        try:
            client.download(s.url, dest)
        except NotFound:
            log.warning("Supplement %s (%s) not found at %s", s.label or s.id, s.href, s.url)
            s.status = "missing"
            continue
        s.path = dest.relative_to(vdir).as_posix()
        s.status = "ok"
        log.info("Supplement %s -> %s (%d KB)", s.label or s.id, s.path, dest.stat().st_size // 1024)
