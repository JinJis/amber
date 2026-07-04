"""QT-2 — number-audit unit tests (the publish trust floor). Pure functions, no network."""

from agentengine.audit import audit_answer, collect_pool, extract_numbers


TOOLS = [{"data": {"close": 185.6, "change_pct": -3.2, "median": 2.81, "revenue": 66_231_000_000,
                   "rows": [{"depth_pct": -56.78}], "note": "상승 마감 비율 60%"}}]


def test_extract_skips_structure_numerals():
    nums = extract_numbers("2026년 7월 2일 기준 3가지 요인, 2분기 실적, 사례는 총 60건 [1, 2]. 20거래일 뒤")
    assert nums == []                                     # dates, ordinals, counts, anchors, horizons


def test_extract_units_and_scales():
    nums = {n["raw"]: n for n in extract_numbers("매출 662억, 낙폭 -56.78%, 종가 $185.6, 시총 2.5조")}
    assert nums["662억"]["value"] == 662e8
    assert nums["-56.78%"]["pct"] is True and abs(nums["-56.78%"]["value"]) == 56.78
    assert nums["$185.6"]["value"] == 185.6
    assert nums["2.5조"]["value"] == 2.5e12


def test_audit_passes_supported_numbers():
    a = audit_answer("종가는 $185.6로 전일 대비 3.2% 하락했고, 중앙값은 +2.81%였다. 최대 낙폭은 56.78%.", TOOLS)
    assert a["unsupported"] == [] and a["checked"] >= 4


def test_audit_catches_rigged_number():
    # the QT-2 acceptance criterion: an unsupported numeral in the draft is caught
    a = audit_answer("종가는 $185.6였고 영업이익률은 무려 47.3%에 달했다.", TOOLS)
    assert "47.3%" in a["unsupported"] and a["supported"] >= 1


def test_audit_display_rounding_and_scale():
    # 66,231,000,000 in the pool supports "662억" (scale) and "약 662.3억" (rounding)
    a = audit_answer("연 매출은 662억 달러였다.", TOOLS)
    assert a["unsupported"] == []
    a2 = audit_answer("연 매출은 약 662.3억 달러 수준이었다.", TOOLS)
    assert a2["unsupported"] == []


def test_pool_walks_nested_and_strings():
    pool = collect_pool(TOOLS)
    assert -56.78 in pool and 2.81 in pool
    assert any(abs(v - 60.0) < 1e-9 for v in pool)        # from the "상승 마감 비율 60%" string


def test_desk_feed_drops_card_with_invented_figure(monkeypatch):
    """QT-2 in the feed: right source, wrong number → the card never ships."""
    import asyncio

    import httpx
    import respx

    import agentengine.deskfeed as DF
    from agentengine.config import settings
    from agentengine.deskfeed import DeskFeedRequest, build_desk_feed

    monkeypatch.setattr(settings, "gateway_url", "http://gw.test")

    async def fake_synth(req, gathered):
        return [
            {"kind": "price_move", "question": "q1", "hook": "AAPL 종가 185.6달러, 전일 대비 -3.2%", "sources": [1]},
            {"kind": "price_move", "question": "q2", "hook": "AAPL이 무려 12.9% 급등했다", "sources": [1]},
        ]

    monkeypatch.setattr(DF, "_synthesize", fake_synth)

    async def run():
        with respx.mock:
            respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(200, json={
                "connectors": [{"id": "yahoo", "name": "Yahoo Finance", "resources": [
                    {"name": "price_snapshot", "method": "GET", "path": "/prices/snapshot",
                     "params": [{"name": "ticker", "required": True}, {"name": "market"}],
                     "provenance": {"source": "Yahoo Finance"}}]}]}))
            respx.route(method="GET", url__regex=r"http://gw\.test/prices/snapshot.*").mock(
                return_value=httpx.Response(200, json={"ticker": "AAPL", "close": 185.6,
                                                       "change_pct": -3.2, "as_of": "2026-07-03"},
                                            headers={"x-connector": "yahoo"}))
            req = DeskFeedRequest(watchlists=[{"name": "t", "items": [
                {"market": "US", "ticker": "AAPL", "name": "Apple"}]}])
            return await build_desk_feed(req, "vgk_x")

    feed = asyncio.run(run())
    qs = [c["question"] for c in feed["cards"]]
    assert "q1" in qs        # supported figures ship
    assert "q2" not in qs    # invented +12.9% → dropped by the audit
