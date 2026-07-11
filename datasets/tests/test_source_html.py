"""Unit tests for the external-source evidence viewer (`app.store.source_html`).

The in-app source viewer fetches an arbitrary public page server-side, sanitizes it, and serves it
same-origin so the web viewer can highlight the cited value. These tests pin the SSRF guard (the
thing that makes fetching arbitrary URLs safe), the fragment stripping, and the sanitize+serve path.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.store import source_html as S


def _addrinfo(ip: str):
    # shape of socket.getaddrinfo entries: (family, type, proto, canonname, sockaddr)
    return [(2, 1, 6, "", (ip, 0))]


# Real pages must clear the visible-text floor (_MIN_VISIBLE_CHARS) — pad mock bodies with a
# plausible paragraph so the tests exercise their own concern, not the floor.
_FILLER = ("<p>Consumer Price Index for All Urban Consumers: seasonally adjusted monthly series, "
           "U.S. city average, all items. Source: U.S. Bureau of Labor Statistics, retrieved from "
           "the public timeseries viewer.</p>")


def test_strip_fragment_drops_text_anchor():
    u = "https://example.com/a/b?q=1#:~:text=hello%20world"
    assert S._strip_fragment(u) == "https://example.com/a/b?q=1"
    # no fragment → unchanged
    assert S._strip_fragment("https://example.com/x") == "https://example.com/x"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                                "::1", "0.0.0.0", "224.0.0.1"])
def test_is_public_host_rejects_private(monkeypatch, ip):
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo(ip))
    assert S._is_public_host("anything.example") is False


def test_is_public_host_accepts_public(monkeypatch):
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    assert S._is_public_host("data.bls.gov") is True


def test_is_public_host_unresolvable_is_refused(monkeypatch):
    def boom(*a, **k):
        raise S.socket.gaierror("nope")
    monkeypatch.setattr(S.socket, "getaddrinfo", boom)
    assert S._is_public_host("does-not-resolve.invalid") is False


def test_safe_rejects_non_http_schemes(monkeypatch):
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    assert S._safe("file:///etc/passwd") is False
    assert S._safe("ftp://example.com/x") is False
    assert S._safe("https://example.com/ok") is True


@pytest.mark.asyncio
async def test_get_source_html_private_host_makes_no_request(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("127.0.0.1"))
    # if it tried to fetch, respx (no routes) would raise; assert it returns None before fetching
    assert await S.get_source_html("http://localhost/evil") is None


@pytest.mark.asyncio
@respx.mock
async def test_get_source_html_sanitizes_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    page = ("<html><head><title>CPI</title>"
            "<meta http-equiv='Content-Security-Policy' content='default-src *'>"
            "<script>alert(1)</script></head>"
            f"<body>{_FILLER}<table><tr><td>323.048</td></tr></table></body></html>")
    route = respx.get("https://data.bls.gov/timeseries/CUSR0000SA0").mock(
        return_value=httpx.Response(200, html=page))

    out = await S.get_source_html("https://data.bls.gov/timeseries/CUSR0000SA0#:~:text=323")
    assert out is not None
    assert "<script" not in out.lower()                 # scripts stripped
    assert "default-src 'none'" in out                  # our strict CSP injected
    assert "default-src *" not in out                   # source's own CSP dropped
    assert "323.048" in out                             # the cited value survives for highlighting
    assert route.called

    # second call is cache-first: served from disk without a second fetch
    respx.reset()
    cached = await S.get_source_html("https://data.bls.gov/timeseries/CUSR0000SA0#:~:text=999")
    assert cached is not None and "323.048" in cached


@pytest.mark.asyncio
@respx.mock
async def test_get_source_html_rejects_non_html(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    respx.get("https://example.com/data.json").mock(
        return_value=httpx.Response(200, json={"x": 1}))
    assert await S.get_source_html("https://example.com/data.json") is None


def _by_host(host_ip: dict[str, str]):
    """A getaddrinfo stub that resolves each host to a chosen IP (so a redirect can cross a
    public→private boundary mid-fetch, exercising the per-hop re-validation)."""
    def _stub(host, *a, **k):
        return _addrinfo(host_ip.get(host, "8.8.8.8"))
    return _stub


@pytest.mark.asyncio
@respx.mock
async def test_redirect_to_private_host_is_refused(monkeypatch, tmp_path):
    # a public page that 302-redirects to an INTERNAL host must NOT be followed (SSRF via redirect).
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo",
                        _by_host({"pub.example": "8.8.8.8", "internal.example": "127.0.0.1"}))
    respx.get("https://pub.example/start").mock(
        return_value=httpx.Response(302, headers={"location": "https://internal.example/secret"}))
    secret = respx.get("https://internal.example/secret").mock(
        return_value=httpx.Response(200, html="<html><body>SECRET</body></html>"))
    assert await S.get_source_html("https://pub.example/start") is None
    assert not secret.called          # the internal host was never fetched


@pytest.mark.asyncio
@respx.mock
async def test_redirect_to_public_host_is_followed(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo",
                        _by_host({"a.example": "8.8.8.8", "b.example": "8.8.4.4"}))
    respx.get("https://a.example/start").mock(
        return_value=httpx.Response(301, headers={"location": "https://b.example/final"}))
    respx.get("https://b.example/final").mock(
        return_value=httpx.Response(200, html=f"<html><body>{_FILLER}323.048</body></html>"))
    out = await S.get_source_html("https://a.example/start")
    assert out is not None and "323.048" in out


@pytest.mark.asyncio
@respx.mock
async def test_too_many_redirects_gives_up(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    # a self-redirect loop → bounded by _MAX_REDIRECTS → None (never a 200)
    respx.get("https://loop.example/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://loop.example/x"}))
    assert await S.get_source_html("https://loop.example/x") is None


@pytest.mark.asyncio
@respx.mock
async def test_oversize_body_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    monkeypatch.setattr(S, "_MAX_BYTES", 64)      # tiny cap for the test
    big = "<html><body>" + ("x" * 500) + "</body></html>"
    respx.get("https://big.example/page").mock(return_value=httpx.Response(200, html=big))
    assert await S.get_source_html("https://big.example/page") is None


@pytest.mark.asyncio
@respx.mock
async def test_non_200_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    respx.get("https://gone.example/x").mock(return_value=httpx.Response(404, html="<html/>"))
    assert await S.get_source_html("https://gone.example/x") is None


@pytest.mark.asyncio
@respx.mock
async def test_http_scheme_public_host_is_allowed(monkeypatch, tmp_path):
    # http (not just https) is fine as long as the host is public.
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    respx.get("http://plain.example/p").mock(
        return_value=httpx.Response(200, html=f"<html><body>{_FILLER}<b>2.5%</b></body></html>"))
    out = await S.get_source_html("http://plain.example/p")
    assert out is not None and "2.5%" in out


@pytest.mark.asyncio
@respx.mock
async def test_sanitize_strips_base_and_injects_csp(monkeypatch, tmp_path):
    # the shared sanitize() removes <base> (so the source's relative urls don't resolve against our
    # origin) and injects the strict CSP; the figure text survives for highlighting. (Inline handlers
    # never fire anyway: the iframe has no allow-scripts and default-src 'none' blocks egress.)
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    page = ("<html><head><base href='https://evil.example/'></head>"
            f"<body>{_FILLER}<p>data 1,234.5</p></body></html>")
    respx.get("https://src.example/p").mock(return_value=httpx.Response(200, html=page))
    out = await S.get_source_html("https://src.example/p")
    assert out is not None
    # the SOURCE's base is stripped; OUR base (the page's own origin) is injected so relative
    # assets resolve inside srcdoc — never the attacker-chosen one.
    assert "evil.example" not in out
    assert '<base href="https://src.example/">' in out
    # external pages get the PASSIVE csp: styles/images may load over https, scripts stay dead
    assert "default-src 'none'" in out and "img-src https: data:" in out
    assert "1,234.5" in out                       # the cited figure survives


# --- visible-text floor (v3) ------------------------------------------------
@pytest.mark.asyncio
async def test_visible_text_floor_refuses_script_shell(monkeypatch, tmp_path):
    # a big page whose *visible* text is tiny (a JS interstitial / consent wall) must not be
    # served — and, crucially, must NOT be cached (a cached shell poisons every future view).
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))
    shell = ("<html><head><title>Google News</title></head><body>"
             + "<div class='shell'><span></span></div>" * 500
             + "<noscript>Google News</noscript></body></html>")
    assert len(shell) > 10_000 and S._visible_chars(shell) < S._MIN_VISIBLE_CHARS

    async def fake_fetch(url):
        return shell
    monkeypatch.setattr(S, "_fetch", fake_fetch)
    assert await S.get_source_html("https://shell.example/interstitial") is None
    assert list(tmp_path.rglob("*.html")) == []   # nothing cached


@pytest.mark.asyncio
async def test_visible_text_at_floor_is_served_and_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))

    async def fake_fetch(url):
        return f"<html><body>{_FILLER}<b>323.048</b></body></html>"
    monkeypatch.setattr(S, "_fetch", fake_fetch)
    out = await S.get_source_html("https://real.example/article")
    assert out is not None and "323.048" in out
    assert S._cache_path("https://real.example/article").exists()

    # second view is cache-first — the network path must not run again
    async def boom(url):
        raise AssertionError("_fetch must not be called on a cache hit")
    monkeypatch.setattr(S, "_fetch", boom)
    cached = await S.get_source_html("https://real.example/article")
    assert cached is not None and "323.048" in cached


def test_cache_key_is_v3_not_v2():
    # the v2-era cache holds useless JS shells stored before the visible-text floor existed;
    # the key MUST differ from v2 so those entries are abandoned, not served.
    import hashlib

    url = "https://pub.example/x"
    v3 = hashlib.sha256(("v3:" + url).encode("utf-8")).hexdigest()[:32]
    v2 = hashlib.sha256(("v2:" + url).encode("utf-8")).hexdigest()[:32]
    p = S._cache_path(url)
    assert p.name == f"{v3}.html"
    assert v2 not in str(p)
    assert p.parent.name == "source"


# --- Google News interstitials ------------------------------------------------
@pytest.mark.asyncio
async def test_gnews_url_is_resolved_then_publisher_page_served(monkeypatch, tmp_path):
    import app.store.gnews_resolve as G

    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(S.socket, "getaddrinfo", lambda *a, **k: _addrinfo("8.8.8.8"))

    async def fake_resolve(url):
        return "https://publisher.example/story#utm=x"
    monkeypatch.setattr(G, "resolve_gnews_url", fake_resolve)

    seen: dict[str, str] = {}

    async def fake_fetch(url):
        seen["url"] = url
        return f"<html><body>{_FILLER}<p>HBM demand tripled.</p></body></html>"
    monkeypatch.setattr(S, "_fetch", fake_fetch)

    gnews = "https://news.google.com/rss/articles/CBMiOPAQUE?oc=5"
    out = await S.get_source_html(gnews)
    assert out is not None and "HBM demand tripled." in out
    # the PUBLISHER url was fetched (fragment stripped), never the interstitial
    assert seen["url"] == "https://publisher.example/story"
    # cached under the CITATION's url so repeat views of the same citation hit the cache
    assert S._cache_path(gnews).exists()


@pytest.mark.asyncio
async def test_gnews_unresolvable_returns_none_without_fetching(monkeypatch, tmp_path):
    import app.store.gnews_resolve as G

    monkeypatch.setattr(S.settings, "evidence_docs_dir", str(tmp_path))

    async def fake_resolve(url):
        return None
    monkeypatch.setattr(G, "resolve_gnews_url", fake_resolve)

    async def boom(url):
        raise AssertionError("the interstitial must never be fetched")
    monkeypatch.setattr(S, "_fetch", boom)
    # honest gap: no resolver result → None (the external link still works in a real browser)
    assert await S.get_source_html("https://news.google.com/articles/CBMiOPAQUE") is None
    assert list(tmp_path.rglob("*.html")) == []


# --- DART viewer canonicalization ----------------------------------------------
@pytest.mark.asyncio
async def test_dart_viewer_url_routes_to_filing_html(monkeypatch):
    # dart.fss.or.kr/dsaf001/main.do?rcpNo=… is a script-driven shell — it must be served through
    # the KR filing path (real document markup), not fetched+sanitized like a generic page.
    import app.store.filing_html as FH

    calls: list[tuple[str, str]] = []

    async def fake_filing(market, rcept_no):
        calls.append((market, rcept_no))
        return "<html><body>사업보고서 본문</body></html>"
    monkeypatch.setattr(FH, "get_filing_html", fake_filing)

    async def boom(url):
        raise AssertionError("a DART viewer link must not hit the generic fetch path")
    monkeypatch.setattr(S, "_fetch", boom)

    out = await S.get_source_html("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250318001192")
    assert calls == [("KR", "20250318001192")]
    assert out == "<html><body>사업보고서 본문</body></html>"


@pytest.mark.asyncio
async def test_dart_viewer_url_without_rcpno_is_a_gap(monkeypatch):
    async def boom(url):
        raise AssertionError("must not fall through to the generic fetch")
    monkeypatch.setattr(S, "_fetch", boom)
    assert await S.get_source_html("https://dart.fss.or.kr/dsaf001/main.do?foo=1") is None
