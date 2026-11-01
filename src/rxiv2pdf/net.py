"""HTTP client: polite throttling, retry/backoff, Cloudflare detection.

Behaviour observed on www.biorxiv.org (Oct 2026) that this module is built around:

* Rate limiting is aggressive: roughly ten page/image requests in a couple of minutes
  gets ``429 text/plain "error code: 1015"`` with ``Retry-After: ~80``. We honour
  Retry-After (up to ``max_wait``) instead of failing.
* ``api.biorxiv.org`` was not rate limited in the same session.
* Missing files are ``404 text/html`` (a normal "Page Not Found" page), so a 404 is
  ``NotFound``, never mistaken for a challenge.
* Old ``/content/early/...`` URLs 301-redirect to ``/content/{server}/early/...``.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

import httpx

from . import __version__

log = logging.getLogger(__name__)

Expect = Literal["json", "xml", "html", "image", "any"]

_RETRY_STATUSES = {429, 500, 502, 503, 504}
# NB: "challenge-platform" is NOT a marker - Cloudflare injects that script into every
# normal bioRxiv page (verified on article, 404 and table-expansion pages).
_CHALLENGE_MARKERS = (b"cf-chl", b"just a moment", b"cf_chl_opt", b"attention required")
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"RIFF")


def user_agent() -> str:
    ua = f"rxiv2pdf/{__version__} (personal preprint-to-PDF reader; python-httpx/{httpx.__version__})"
    contact = os.environ.get("RXIV2PDF_CONTACT")
    return f"{ua} mailto:{contact}" if contact else ua


class FetchError(Exception):
    """Any failure to retrieve something. ``str(err)`` is meant for the user."""


class NotFound(FetchError):
    def __init__(self, url: str, status: int = 404):
        super().__init__(f"Not found (HTTP {status}): {url}")
        self.url = url
        self.status = status


class Blocked(FetchError):
    """Cloudflare served a challenge / firewall page instead of the content."""


class RateLimited(FetchError):
    def __init__(self, message: str, retry_after: float | None):
        super().__init__(message)
        self.retry_after = retry_after


class UnexpectedContent(FetchError):
    """The server answered 200 but with the wrong kind of document."""


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _content_type(resp: httpx.Response) -> str:
    return resp.headers.get("content-type", "").split(";")[0].strip().lower()


def _looks_html(resp: httpx.Response, head: bytes) -> bool:
    if _content_type(resp) in ("text/html", "application/xhtml+xml"):
        return True
    start = head.lstrip()[:64].lower()
    return start.startswith((b"<!doctype html", b"<html"))


def _is_challenge(resp: httpx.Response, head: bytes) -> bool:
    if resp.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    low = head[:20000].lower()
    if resp.status_code == 403 and b"error code: 10" in low:  # 1020 = firewall rule
        return True
    return _looks_html(resp, head) and any(m in low for m in _CHALLENGE_MARKERS)


def _site(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return "medRxiv" if "medrxiv" in host else "bioRxiv" if "biorxiv" in host else host or "The server"


def _blocked_message(url: str) -> str:
    return (
        f"Cloudflare returned a challenge page instead of content for {url}. "
        f"{_site(url)} is blocking automated access from this network right now. "
        "Wait a while and retry; files already downloaded are cached, so the next run resumes."
    )


class Client:
    """Thin wrapper around ``httpx.Client``. Inject ``transport``/``sleep``/``clock`` in tests."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = 1.0,
        max_retries: int = 4,
        max_wait: float = 300.0,  # Retry-After escalates on repeat hits (83s ... 240s observed)
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": user_agent()},
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=15.0),
            transport=transport,
        )
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self.request_count = 0  # every HTTP request actually sent, incl. retries

    # -- context manager -------------------------------------------------------------
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- public API ------------------------------------------------------------------
    def get(self, url: str, *, expect: Expect = "any") -> httpx.Response:
        """GET ``url`` fully into memory and check it is the kind of document expected."""
        return self._request(url, expect=expect, dest=None)

    def download(self, url: str, dest: Path, *, expect: Expect = "any") -> httpx.Response:
        """Stream ``url`` to ``dest`` (atomically via a .part file). Body is not kept in memory."""
        return self._request(url, expect=expect, dest=dest)

    # -- internals -------------------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self.min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)

    def _mark(self, url: str) -> None:
        self._last_request[urlsplit(url).hostname or ""] = self._clock()

    def _backoff(self, attempt: int) -> float:
        return min(self.max_wait, 2.0 ** attempt + random.uniform(0, 0.5))

    def _send(self, url: str, dest: Path | None) -> tuple[httpx.Response, bytes, bool]:
        """Send one request. Returns (response, first bytes of body for sniffing, whether the
        body was streamed to ``dest``). An error or an HTML page (it may be a Cloudflare
        challenge) is read into memory instead, to be checked before anything is written."""
        self._throttle(url)
        self.request_count += 1
        try:
            resp = self._http.send(self._http.build_request("GET", url), stream=True)
            self.request_count += len(resp.history)  # followed redirects are requests too
        finally:
            self._mark(url)
        try:
            streamable = dest is not None and resp.status_code == 200 and _content_type(resp) != "text/html"
            if not streamable:
                resp.read()
                return resp, resp.content[:20000], False
            assert dest is not None
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            head = b""
            with part.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    if len(head) < 20000:
                        head += chunk[: 20000 - len(head)]
                    fh.write(chunk)
            part.replace(dest)
            return resp, head, True
        finally:
            resp.close()

    def _request(self, url: str, *, expect: Expect, dest: Path | None) -> httpx.Response:
        attempt = 0
        while True:
            try:
                resp, head, streamed = self._send(url, dest)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    host = urlsplit(url).hostname or url
                    if isinstance(exc, httpx.TimeoutException):
                        raise FetchError(f"Timed out talking to {host} after {attempt + 1} attempts ({url}). The server may be slow; try again later.") from exc
                    raise FetchError(f"Could not connect to {host} ({type(exc).__name__}: {exc}). Check your internet connection.") from exc
                wait = self._backoff(attempt)
                log.warning("Network error (%s); retrying in %.0fs: %s", exc.__class__.__name__, wait, url)
                self._sleep(wait)
                attempt += 1
                continue

            status = resp.status_code
            # A 429 is rate limiting whatever its body says: Cloudflare sends curl a plain
            # "error code: 1015" but sends httpx an HTML "Attention Required | Cloudflare" page
            # (observed live), which would otherwise look like a challenge.
            if status != 429 and _is_challenge(resp, head):
                raise Blocked(_blocked_message(url))

            if status in _RETRY_STATUSES:
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None and retry_after < 1:
                    # Seen in a batch run: bioRxiv's 1015 answer with "Retry-After: 0", four
                    # times in a row - retrying at once just uses up the retries. Back off.
                    retry_after = None
                if status == 429:
                    wait = retry_after if retry_after is not None else min(self.max_wait, 10.0 * 2.0**attempt)
                else:
                    wait = retry_after if retry_after is not None else self._backoff(attempt)
                if attempt >= self.max_retries or wait > self.max_wait:
                    if status == 429:
                        hint = f" Retry in about {wait:.0f}s." if wait else ""
                        raise RateLimited(
                            f"{_site(url)} is rate-limiting requests (HTTP 429, Cloudflare error 1015) for {url}.{hint} "
                            "Already-downloaded files are cached, so re-running resumes where this stopped.",
                            retry_after=retry_after,
                        )
                    raise FetchError(f"Server error HTTP {status} for {url} after {attempt + 1} attempts")
                what = "Rate-limited by server (HTTP 429)" if status == 429 else f"Server error HTTP {status}"
                log.warning("%s; waiting %.0fs before retry %d/%d", what, wait, attempt + 1, self.max_retries)
                self._sleep(wait)
                attempt += 1
                continue

            if status in (404, 410):
                raise NotFound(url, status)
            if status >= 400:
                raise FetchError(f"HTTP {status} fetching {url}")

            self._check_expect(resp, head, url, expect)
            if dest is not None and not streamed:  # an HTML file, read into memory to be checked
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(dest.name + ".part")
                part.write_bytes(resp.content)
                part.replace(dest)
            return resp

    def _check_expect(self, resp: httpx.Response, head: bytes, url: str, expect: Expect) -> None:
        if expect in ("any", "html"):
            return
        ctype = _content_type(resp)
        if _looks_html(resp, head):
            raise UnexpectedContent(
                f"Expected {expect.upper()} but got an HTML page from {url}. "
                "This is usually a Cloudflare interstitial or an error page; try again later."
            )
        if expect == "json":
            if "json" not in ctype and not head.lstrip().startswith((b"{", b"[")):
                raise UnexpectedContent(f"Expected JSON but got {ctype or 'unknown content'} from {url}")
        elif expect == "xml":
            start = head.lstrip()[:200]
            if "xml" not in ctype and not start.startswith((b"<?xml", b"<article", b"<!DOCTYPE article")):
                raise UnexpectedContent(f"Expected XML but got {ctype or 'unknown content'} from {url}")
        elif expect == "image" and not ctype.startswith("image/") and not head.startswith(_IMAGE_MAGIC):
            raise UnexpectedContent(f"Expected an image but got {ctype or 'unknown content'} from {url}")
