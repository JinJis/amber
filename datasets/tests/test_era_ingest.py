"""HL-5b — era-news RAG ingest: source-by-coverage routing, regime scoping, honest gaps."""

from __future__ import annotations

from datetime import date

import httpx
import respx

import app.store.era_news_ingest as EN


def test_docs_scope_to_regime_and_carry_era_type():
    reg = {"slug": "gfc-2008", "name_en": "GFC", "market": "US",
           "start_date": "2008-09-01", "end_date": "2009-03-01"}
    docs = EN._to_docs(reg, [
        {"title": "Lehman files for bankruptcy", "abstract": "the largest ever", "url": "https://x/1", "date": "2008-09-15"},
        {"title": "", "url": "https://x/2"},   # no title → dropped
    ], "GDELT DOC 2.0")
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_type"] == "era_news" and d["ticker"] == "gfc-2008"   # scoped to the regime
    assert d["as_of"] == "2008-09-15" and "bankruptcy" in d["text"] and "largest ever" in d["text"]
    assert d["doc_id"].startswith("era:gfc-2008:")


def test_query_map_and_fallback():
    assert EN._query_for({"slug": "dotcom-bust"}) == "dot-com Nasdaq crash"
    assert EN._query_for({"slug": "unknown", "name_en": "Foo Crisis"}) == "Foo Crisis"


@respx.mock
async def test_recent_regime_uses_gdelt():
    reg = {"slug": "svb-2023", "name_en": "SVB", "market": "US",
           "start_date": "2023-03-08", "end_date": "2023-03-20"}
    respx.get(url__regex=r"https://api\.gdeltproject\.org/.*").mock(return_value=httpx.Response(200, json={
        "articles": [{"title": "SVB collapses", "url": "https://x", "seendate": "20230310T120000Z"}]}))
    arts, source = await EN._articles_for(reg)
    assert source == "GDELT DOC 2.0" and arts and arts[0]["title"] == "SVB collapses"


async def test_pre_2017_without_nyt_key_draws_a_gap(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "nyt_api_key", "")
    reg = {"slug": "dotcom-bust", "name_en": "Dotcom", "market": "US",
           "start_date": "2000-03-10", "end_date": "2002-10-09"}
    arts, source = await EN._articles_for(reg)   # GDELT floor is 2017 → NYT path → no key → gap
    assert arts == [] and "NYT_API_KEY" in source


def test_era_news_pipeline_registered():
    from app.pipelines import PIPELINE_BY_ID
    p = PIPELINE_BY_ID.get("era_news")
    assert p and p["default"] is False and p["kind"] == "era_news" and p["markets"] == ["US"]
