"""A fake bioRxiv that serves the real saved fixtures and mimics observed server behaviour.

Behaviour reproduced from live probing (Oct 2026):
* API jatsxml ``/content/early/...`` 301-redirects to ``/content/biorxiv/early/...``.
* For the 2026 paper the API jatsxml URL (dated 10/20) 404s; the XML lives under 10/19,
  which only the full-text page's ``citation_pdf_url`` reveals.
* Images: ``{base}/F1.large.jpg`` and ``{base}/T1/graphic-5.large.jpg``; everything else 404s
  with an HTML "Page Not Found" page (which contains Cloudflare's injected script, like the real one).
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from PIL import Image

from rxiv2pdf.net import Client

FIXTURES = Path(__file__).parent / "fixtures"

OLD_DOI = "10.1101/2020.03.22.002386"
OLD_DIR = FIXTURES / "2020.03.22.002386"
NEW_DOI = "10.64898/2026.10.16.759679"
NEW_DIR = FIXTURES / "2026.10.16.759679"
EQN_DOI = "10.64898/2026.10.22.760766"  # has disp-formula images + Extended Data figures
EQN_DIR = FIXTURES / "2026.10.22.760766"

NOT_FOUND_HTML = (
    b"<!DOCTYPE html><html><head><title>Page Not Found | bioRxiv</title>"
    b'<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></head><body>404</body></html>'
)

_IMAGE_RE = re.compile(r"/content/(bio|med)rxiv/early/\d{4}/\d{2}/\d{2}/[^/]+/(F\d+|T\d+/graphic-\d+)\.large\.jpg$")
_EMBED_RE = re.compile(r"^/sites/default/files/highwire/(bio|med)rxiv/early/\d{4}/\d{2}/\d{2}/[^/]+/embed/graphic-\d+\.gif$")
_SUPP_RE = re.compile(r"/content/(bio|med)rxiv/early/\d{4}/\d{2}/\d{2}/[^/]+/DC\d+/embed/media-\d+\.\w+$")


def tiny_jpeg(size: tuple[int, int] = (40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def tiny_gif(size: tuple[int, int] = (60, 12)) -> bytes:
    buf = io.BytesIO()
    Image.new("P", size, 1).save(buf, format="GIF")
    return buf.getvalue()


Handler = Callable[[httpx.Request], httpx.Response | None]


class FakeRxiv:
    def __init__(self) -> None:
        self.routes: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.overrides: list[Handler] = []  # tried first; return None to fall through
        self.requests: list[str] = []
        self.image_bytes = tiny_jpeg()
        self.serve_images = True
        self.serve_supplements = True
        self.supplement_bytes = b"%PDF-1.4 fake supplement"

    # -- route helpers ---------------------------------------------------------------
    def add(self, url: str, status: int = 200, body: bytes = b"", ctype: str = "text/plain", headers: dict | None = None) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(status, content=body, headers={"content-type": ctype, **(headers or {})})

        self.routes[url] = handler

    def add_json(self, url: str, path: Path) -> None:
        self.add(url, body=path.read_bytes(), ctype="application/json")

    def add_xml(self, url: str, path: Path) -> None:
        self.add(url, body=path.read_bytes(), ctype="application/xml")

    def redirect(self, url: str, to: str) -> None:
        self.add(url, status=301, ctype="text/html; charset=UTF-8", headers={"location": to})

    # -- transport -------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        for override in self.overrides:
            resp = override(request)
            if resp is not None:
                return resp
        if url in self.routes:
            return self.routes[url](request)
        path = request.url.path
        if self.serve_images and _IMAGE_RE.search(path):
            return httpx.Response(200, content=self.image_bytes, headers={"content-type": "image/jpeg"})
        if self.serve_images and _EMBED_RE.search(path):
            return httpx.Response(200, content=tiny_gif(), headers={"content-type": "image/gif"})
        if self.serve_supplements and _SUPP_RE.search(path):
            return httpx.Response(200, content=self.supplement_bytes, headers={"content-type": "application/pdf"})
        return httpx.Response(404, content=NOT_FOUND_HTML, headers={"content-type": "text/html; charset=utf-8"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def client(self, **kwargs) -> Client:
        kwargs.setdefault("min_interval", 0)
        kwargs.setdefault("sleep", lambda s: None)
        return Client(transport=self.transport, **kwargs)


@pytest.fixture
def fake() -> FakeRxiv:
    f = FakeRxiv()
    api = "https://api.biorxiv.org/details"

    # Old 10.1101 paper, 3 versions; v3 XML via the redirecting API URL.
    f.add_json(f"{api}/biorxiv/{OLD_DOI}", OLD_DIR / "api.json")
    f.add_json(f"{api}/medrxiv/{OLD_DOI}", FIXTURES / "api_no_posts_found.json")
    f.redirect(
        "https://www.biorxiv.org/content/early/2020/03/27/2020.03.22.002386.source.xml",
        "https://www.biorxiv.org/content/biorxiv/early/2020/03/27/2020.03.22.002386.source.xml",
    )
    f.add_xml("https://www.biorxiv.org/content/biorxiv/early/2020/03/27/2020.03.22.002386.source.xml", OLD_DIR / "v3.source.xml")

    # New 10.64898 paper: API jatsxml URL 404s (unrouted), real XML found via the HTML page.
    f.add_json(f"{api}/biorxiv/{NEW_DOI}", NEW_DIR / "api.json")
    f.add(
        "https://www.biorxiv.org/content/10.64898/2026.10.16.759679v1.full",
        body=(NEW_DIR / "v1.full.reduced.html").read_bytes(),
        ctype="text/html; charset=utf-8",
    )
    f.add_xml("https://www.biorxiv.org/content/biorxiv/early/2026/10/19/2026.10.16.759679.source.xml", NEW_DIR / "v1.source.xml")

    # Paper with display-formula images (fetched live 2026-10-31).
    f.add_json(f"{api}/biorxiv/{EQN_DOI}", EQN_DIR / "api.json")
    f.redirect(
        "https://www.biorxiv.org/content/early/2026/10/27/2026.10.22.760766.source.xml",
        "https://www.biorxiv.org/content/biorxiv/early/2026/10/27/2026.10.22.760766.source.xml",
    )
    f.add_xml("https://www.biorxiv.org/content/biorxiv/early/2026/10/27/2026.10.22.760766.source.xml", EQN_DIR / "v1.source.xml")
    return f


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"
