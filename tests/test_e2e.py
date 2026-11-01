"""Live tests against the real servers. Excluded by default; run with ``pytest -m network``.

They are slow on purpose: bioRxiv rate-limits after roughly ten requests, and the client
waits out ``Retry-After`` rather than failing.
"""

import pytest

from rxiv2pdf.fetch import fetch
from rxiv2pdf.net import Client
from rxiv2pdf.resolve import resolve

pytestmark = pytest.mark.network


def test_live_fetch_10_1101_with_tables(tmp_path):
    with Client() as client:
        res = fetch(resolve("10.1101/2020.03.22.002386v3"), client=client, cache_root=tmp_path)
    m = res.manifest
    assert m.version == 3 and m.jats_strategy in ("api-jatsxml", "fulltext-html-meta")
    assert res.xml_path.read_bytes().lstrip().startswith(b"<")
    assert len(m.figures) == 15 and all(f.status == "ok" for f in m.figures)
    assert all((res.version_dir / f.path).stat().st_size > 10_000 for f in m.figures)
