import httpx
import pytest

from rxiv2pdf.net import (
    Blocked,
    Client,
    FetchError,
    NotFound,
    RateLimited,
    UnexpectedContent,
    user_agent,
)

URL = "https://www.biorxiv.org/content/x.source.xml"
XML = b'<?xml version="1.0"?><article/>'


def make(responses, **kwargs):
    """Client whose transport returns ``responses`` in order (last one repeats)."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request):
        seen.append(request)
        r = responses[min(len(seen), len(responses)) - 1]
        return r() if callable(r) else r

    kwargs.setdefault("min_interval", 0)
    client = Client(transport=httpx.MockTransport(handler), sleep=sleeps.append, **kwargs)
    return client, seen, sleeps


def xml_ok():
    return httpx.Response(200, content=XML, headers={"content-type": "application/xml"})


def rate_limited(retry_after="83"):
    headers = {"content-type": "text/plain; charset=UTF-8"}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return httpx.Response(429, content=b"error code: 1015", headers=headers)


def test_sends_descriptive_user_agent(monkeypatch):
    monkeypatch.delenv("RXIV2PDF_CONTACT", raising=False)
    client, seen, _ = make([xml_ok()])
    client.get(URL, expect="xml")
    ua = seen[0].headers["user-agent"]
    assert ua.startswith("rxiv2pdf/") and "preprint" in ua and "mailto" not in ua


def test_contact_env_var_is_opt_in(monkeypatch):
    monkeypatch.setenv("RXIV2PDF_CONTACT", "me@example.org")
    assert user_agent().endswith("mailto:me@example.org")


def test_honours_retry_after_on_429_then_succeeds():
    client, _, sleeps = make([rate_limited("83"), xml_ok()])
    resp = client.get(URL, expect="xml")
    assert resp.content == XML
    assert sleeps == [83.0]
    assert client.request_count == 2


def test_html_429_attention_required_page_is_rate_limit_not_challenge():
    # What Cloudflare actually sends python-httpx (curl gets text/plain "error code: 1015").
    page = httpx.Response(
        429,
        content=b'<!DOCTYPE html><html lang="en"><head><title>Attention Required | Cloudflare</title></head>'
        b"<body>We have received a high number of requests from this session.</body></html>",
        headers={"content-type": "text/html; charset=UTF-8", "retry-after": "90"},
    )
    client, _, sleeps = make([page, xml_ok()])
    assert client.get(URL, expect="xml").content == XML
    assert sleeps == [90.0]


def test_429_beyond_max_wait_raises_clear_message():
    client, seen, sleeps = make([rate_limited("600")], max_wait=180)
    with pytest.raises(RateLimited) as exc:
        client.get(URL)
    assert "rate-limiting" in str(exc.value) and "600s" in str(exc.value)
    assert exc.value.retry_after == 600
    assert sleeps == [] and len(seen) == 1


def test_429_gives_up_after_max_retries():
    client, seen, sleeps = make([rate_limited("5")], max_retries=2)
    with pytest.raises(RateLimited):
        client.get(URL)
    assert len(seen) == 3 and sleeps == [5.0, 5.0]


def test_429_without_retry_after_backs_off():
    client, _, sleeps = make([rate_limited(None), rate_limited(None), xml_ok()])
    client.get(URL)
    assert sleeps == [10.0, 20.0]


def test_429_with_retry_after_zero_backs_off_instead_of_hammering():
    # Seen live: four "Retry-After: 0" answers in a row used up all retries at once.
    client, _, sleeps = make([rate_limited("0"), rate_limited("0"), xml_ok()])
    client.get(URL)
    assert sleeps == [10.0, 20.0]


def test_5xx_retried_with_backoff():
    client, seen, sleeps = make([httpx.Response(503), httpx.Response(502), xml_ok()])
    client.get(URL, expect="xml")
    assert len(seen) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_transport_error_retried_then_fails():
    def boom():
        raise httpx.ConnectError("nope")

    client, seen, _ = make([boom], max_retries=1)
    with pytest.raises(FetchError, match="Could not connect to www.biorxiv.org .*Check your internet connection"):
        client.get(URL)
    assert len(seen) == 2


def test_404_html_is_not_found_not_a_challenge():
    # Real bioRxiv 404 pages are HTML that include Cloudflare's injected challenge-platform script.
    body = b'<!DOCTYPE html><html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>Page Not Found</html>'
    client, seen, _ = make([httpx.Response(404, content=body, headers={"content-type": "text/html"})])
    with pytest.raises(NotFound):
        client.get(URL, expect="xml")
    assert len(seen) == 1  # no retries on 404


def test_cloudflare_challenge_header_is_blocked():
    resp = httpx.Response(
        403,
        content=b"<!DOCTYPE html><html><title>Just a moment...</title></html>",
        headers={"content-type": "text/html", "cf-mitigated": "challenge"},
    )
    client, _, _ = make([resp])
    with pytest.raises(Blocked, match="Cloudflare"):
        client.get(URL, expect="xml")


def test_challenge_page_with_200_status_is_blocked():
    resp = httpx.Response(
        200,
        content=b"<!DOCTYPE html><html><head><title>Just a moment...</title><script>window._cf_chl_opt={}</script>",
        headers={"content-type": "text/html"},
    )
    client, _, _ = make([resp])
    with pytest.raises(Blocked):
        client.get(URL, expect="image")


def test_firewall_1020_is_blocked():
    client, _, _ = make([httpx.Response(403, content=b"error code: 1020", headers={"content-type": "text/plain"})])
    with pytest.raises(Blocked):
        client.get(URL)


def test_html_where_xml_expected():
    resp = httpx.Response(200, content=b"<!DOCTYPE html><html>hello</html>", headers={"content-type": "text/html"})
    client, _, _ = make([resp])
    with pytest.raises(UnexpectedContent, match="Expected XML but got an HTML page"):
        client.get(URL, expect="xml")


def test_image_sniffed_by_magic_when_content_type_wrong():
    resp = httpx.Response(200, content=b"\xff\xd8\xff\xe0rest", headers={"content-type": "application/octet-stream"})
    client, _, _ = make([resp])
    assert client.get(URL, expect="image").content.startswith(b"\xff\xd8")


def test_follows_redirects():
    old = "https://www.biorxiv.org/content/early/2020/03/27/x.source.xml"
    new = "https://www.biorxiv.org/content/biorxiv/early/2020/03/27/x.source.xml"

    def handler(request):
        if str(request.url) == old:
            return httpx.Response(301, headers={"location": new})
        return xml_ok()

    client = Client(transport=httpx.MockTransport(handler), min_interval=0)
    resp = client.get(old, expect="xml")
    assert str(resp.url) == new


def test_throttles_per_host():
    now = [100.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    client = Client(
        transport=httpx.MockTransport(lambda r: xml_ok()),
        min_interval=1.5,
        sleep=sleep,
        clock=lambda: now[0],
    )
    client.get("https://www.biorxiv.org/a")
    client.get("https://www.biorxiv.org/b")
    client.get("https://api.biorxiv.org/c")  # different host: no wait
    assert sleeps == [1.5]


def test_download_streams_to_file(tmp_path):
    client, _, _ = make([httpx.Response(200, content=b"PK\x03\x04zip", headers={"content-type": "application/zip"})])
    dest = tmp_path / "sub" / "file.zip"
    client.download(URL, dest)
    assert dest.read_bytes() == b"PK\x03\x04zip"
    assert not (tmp_path / "sub" / "file.zip.part").exists()


def test_download_writes_an_html_file(tmp_path):
    # HTML is read into memory to be checked for a challenge first; it must still reach dest.
    body = b"<!DOCTYPE html><html><body>Supplementary table</body></html>"
    client, _, _ = make([httpx.Response(200, content=body, headers={"content-type": "text/html"})])
    dest = tmp_path / "sub" / "table.html"
    client.download(URL, dest)
    assert dest.read_bytes() == body
    assert not (tmp_path / "sub" / "table.html.part").exists()


def test_download_never_writes_a_challenge_page(tmp_path):
    resp = httpx.Response(200, content=b"<html><title>Just a moment...</title>cf_chl_opt", headers={"content-type": "text/html"})
    client, _, _ = make([resp])
    dest = tmp_path / "file.pdf"
    with pytest.raises(Blocked):
        client.download(URL, dest)
    assert not dest.exists()


def test_rate_limit_message_names_the_right_server():
    client, _, _ = make([rate_limited("900")])
    with pytest.raises(RateLimited, match=r"^medRxiv is rate-limiting"):
        client.get("https://www.medrxiv.org/content/x.source.xml")


def test_timeout_message():
    def slow():
        raise httpx.ReadTimeout("slow")

    client, _, _ = make([slow], max_retries=0)
    with pytest.raises(FetchError, match="Timed out talking to www.biorxiv.org"):
        client.get(URL)
