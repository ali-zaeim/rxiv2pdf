"""Stage 1 - Resolve: turn a URL or DOI into (server, doi, version).

Observed in Oct 2026: bioRxiv/medRxiv mint new DOIs under the
``10.64898`` prefix; older preprints keep ``10.1101``. Both are accepted, as is any
``10.NNNN/`` prefix followed by an rxiv-style suffix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlsplit

Server = Literal["biorxiv", "medrxiv"]
SERVERS: tuple[Server, ...] = ("biorxiv", "medrxiv")
DEFAULT_SERVER: Server = "biorxiv"

ServerSource = Literal["flag", "url", "default"]

# Suffix forms:
#   2020.03.22.002386      (bioRxiv since Dec 2019: date + 6 digits)
#   2020.03.22.20040758    (medRxiv: date + 8 digits)
#   002386 / 19001234      (pre-2020 numeric-only suffixes)
_DOI_RE = re.compile(
    r"""
    (?P<doi>10\.\d{4,9}/(?:\d{4}\.\d{2}\.\d{2}\.)?\d{6,})
    (?:v(?P<version>\d+))?
    (?![\w])            # stop at '.', '/', '?', end - not mid-token
    """,
    re.VERBOSE,
)

_DOI_HOSTS = {"doi.org", "dx.doi.org"}


class ResolveError(ValueError):
    """Input could not be understood as a bioRxiv/medRxiv URL or DOI."""


class UnsupportedServer(ResolveError):
    """Input names a preprint server rxiv2pdf cannot work with."""


# ChemRxiv DOIs: 10.26434/chemrxiv.14781567.v1, 10.26434/chemrxiv-2023-xxxxx,
# 10.26434/chemrxiv.15008199/v1. ChemRxiv publishes only the authors' PDFs: its Crossref
# records carry a PDF link and no XML/HTML full text (checked Oct 2026).
_CHEMRXIV_RE = re.compile(r"(?i)10\.26434/chemrxiv[.\-]|(^|[/.])chemrxiv\.org(/|$)")
_CHEMRXIV_MESSAGE = (
    "ChemRxiv preprints are not supported: ChemRxiv publishes only the authors' PDFs, and "
    "rxiv2pdf rebuilds papers from the structured JATS XML that bioRxiv and medRxiv provide."
)


@dataclass(frozen=True)
class Resolved:
    server: Server
    doi: str
    version: int | None  # None means "latest"
    server_source: ServerSource

    @property
    def suffix(self) -> str:
        """The part after the DOI prefix, e.g. ``2020.03.22.002386``."""
        return self.doi.split("/", 1)[1]


def _server_from_host(host: str) -> Server | None:
    host = host.lower().rstrip(".")
    for server in SERVERS:
        if host == f"{server}.org" or host.endswith(f".{server}.org"):
            return server
    return None


def resolve(raw: str, *, server: str | None = None, version: int | None = None) -> Resolved:
    """Parse ``raw`` (URL or DOI). ``server``/``version`` are CLI overrides and win."""
    text = raw.strip()
    if not text:
        raise ResolveError("Empty input: give a bioRxiv/medRxiv URL or a DOI such as 10.1101/2020.03.22.002386")

    if server is not None and server not in SERVERS:
        raise ResolveError(f"Unknown server {server!r}; expected one of: {', '.join(SERVERS)}")
    if version is not None and version < 1:
        raise ResolveError(f"Version must be >= 1, got {version}")
    if _CHEMRXIV_RE.search(unquote(text)):
        raise UnsupportedServer(_CHEMRXIV_MESSAGE)

    url_server: Server | None = None
    haystack = text
    if text.lower().startswith("doi:"):
        haystack = text[4:].strip()
    elif "://" in text or re.match(r"(?i)^(www\.)?(bio|med)rxiv\.org/|^(dx\.)?doi\.org/", text):
        parts = urlsplit(text if "://" in text else f"https://{text}")
        host = (parts.hostname or "").lower()
        url_server = _server_from_host(host)
        if url_server is None and host not in _DOI_HOSTS:
            raise ResolveError(f"Not a bioRxiv/medRxiv URL (host {host!r}): {raw}")
        haystack = unquote(parts.path)  # drops ?query and #fragment; doi.org may encode '/'

    match = _DOI_RE.search(haystack)
    if not match:
        raise ResolveError(
            f"No bioRxiv/medRxiv DOI found in {raw!r}. Expected something like "
            "10.1101/2020.03.22.002386 or https://www.biorxiv.org/content/10.1101/2020.03.22.002386v2"
        )

    url_version = int(match["version"]) if match["version"] else None
    if url_version == 0:
        raise ResolveError(f"Version must be >= 1 in {raw!r}")

    if server is not None:
        chosen: Server = server  # type: ignore[assignment]
        source: ServerSource = "flag"
    elif url_server is not None:
        chosen, source = url_server, "url"
    else:
        chosen, source = DEFAULT_SERVER, "default"

    return Resolved(
        server=chosen,
        doi=match["doi"],
        version=version if version is not None else url_version,
        server_source=source,
    )
