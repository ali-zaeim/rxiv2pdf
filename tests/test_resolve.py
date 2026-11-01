import pytest

from rxiv2pdf.resolve import Resolved, ResolveError, UnsupportedServer, resolve

D = "10.1101/2024.01.15.575123"

CASES = [
    # (input, kwargs, server, doi, version, server_source)
    (D, {}, "biorxiv", D, None, "default"),
    (f"{D}v2", {}, "biorxiv", D, 2, "default"),
    (f"doi:{D}", {}, "biorxiv", D, None, "default"),
    (f"  {D}\n", {}, "biorxiv", D, None, "default"),
    (f"https://www.biorxiv.org/content/{D}v2", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}v2.full", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}v2.full-text", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}v2.full.pdf", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}v2.full.pdf+html", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}v12.supplementary-material?versioned=true", {}, "biorxiv", D, 12, "url"),
    (f"https://www.biorxiv.org/content/{D}v2.full#sec-3", {}, "biorxiv", D, 2, "url"),
    (f"https://www.biorxiv.org/content/{D}", {}, "biorxiv", D, None, "url"),
    (f"https://www.biorxiv.org/content/{D}.full", {}, "biorxiv", D, None, "url"),
    (f"http://biorxiv.org/content/{D}v1/", {}, "biorxiv", D, 1, "url"),
    (f"www.biorxiv.org/content/{D}v3", {}, "biorxiv", D, 3, "url"),
    (f"https://connect.biorxiv.org/relate/content/{D}v1", {}, "biorxiv", D, 1, "url"),
    # medRxiv: 8-digit suffix
    ("https://www.medrxiv.org/content/10.1101/2020.03.22.20040758v1.full.pdf", {}, "medrxiv", "10.1101/2020.03.22.20040758", 1, "url"),
    ("10.1101/2020.03.22.20040758", {}, "biorxiv", "10.1101/2020.03.22.20040758", None, "default"),
    # New DOI prefix minted since late 2025
    ("https://www.biorxiv.org/content/10.64898/2026.10.16.759679v1.full", {}, "biorxiv", "10.64898/2026.10.16.759679", 1, "url"),
    ("10.64898/2026.10.16.759679", {}, "biorxiv", "10.64898/2026.10.16.759679", None, "default"),
    # Pre-2020 numeric suffixes
    ("https://www.biorxiv.org/content/10.1101/123456v1", {}, "biorxiv", "10.1101/123456", 1, "url"),
    ("10.1101/19001234", {}, "biorxiv", "10.1101/19001234", None, "default"),
    # doi.org links (slash may be percent-encoded)
    (f"https://doi.org/{D}", {}, "biorxiv", D, None, "default"),
    ("https://doi.org/10.1101%2F2024.01.15.575123", {}, "biorxiv", D, None, "default"),
    # Flags override URL
    (f"https://www.biorxiv.org/content/{D}v2", {"version": 5}, "biorxiv", D, 5, "url"),
    (f"https://www.biorxiv.org/content/{D}v2", {"server": "medrxiv"}, "medrxiv", D, 2, "flag"),
    (D, {"server": "medrxiv", "version": 1}, "medrxiv", D, 1, "flag"),
]


@pytest.mark.parametrize(("raw", "kwargs", "server", "doi", "version", "source"), CASES)
def test_resolve_table(raw, kwargs, server, doi, version, source):
    assert resolve(raw, **kwargs) == Resolved(server, doi, version, source)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a doi",
        "10.1038/s41586-020-2286-9",  # journal DOI, not a preprint
        "https://www.nature.com/articles/10.1101/2024.01.15.575123",  # wrong host
        "https://www.biorxiv.org/collection/neuroscience",
        "10.1101/2024.01.15.575123v0",
    ],
)
def test_resolve_rejects(raw):
    with pytest.raises(ResolveError):
        resolve(raw)


def test_bad_flags():
    with pytest.raises(ResolveError):
        resolve(D, server="arxiv")
    with pytest.raises(ResolveError):
        resolve(D, version=0)


def test_suffix():
    assert resolve(D).suffix == "2024.01.15.575123"


@pytest.mark.parametrize(
    "raw",
    [
        "10.26434/chemrxiv.14781567.v1",  # figshare-era DOI
        "10.26434/chemrxiv-2023-abc12",  # Cambridge Open Engage DOI
        "10.26434/chemrxiv.15008199/v1",  # current DOI (Crossref, Sept 2026)
        "https://doi.org/10.26434/chemrxiv.15008199/v1",
        "https://doi.org/10.26434%2Fchemrxiv-2023-abc12",
        "https://chemrxiv.org/engage/chemrxiv/article-details/64f0a2b3c4d5e6f7a8b9c0d1",
        "https://chemrxiv.org/doi/full/10.26434/chemrxiv.15008199/v1",
        "chemrxiv.org/engage/chemrxiv/article-details/64f0a2b3",
    ],
)
def test_chemrxiv_is_rejected_with_a_clear_reason(raw):
    with pytest.raises(UnsupportedServer, match="ChemRxiv publishes only the authors' PDFs"):
        resolve(raw)


def test_chemrxiv_check_does_not_catch_lookalikes():
    assert resolve("https://www.biorxiv.org/content/10.1101/2024.01.15.575123v1").server == "biorxiv"
    with pytest.raises(ResolveError) as exc:
        resolve("https://notchemrxiv.org.example.com/x")
    assert not isinstance(exc.value, UnsupportedServer)
