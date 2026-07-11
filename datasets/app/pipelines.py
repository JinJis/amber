"""Data-collection pipeline registry (PH-PIPE).

ONE declarative list of every periodic data pipeline the platform runs — what it
collects, from which source, into which store, and the runner that does it. The
scheduler and the admin backfill UI both derive from this (single source of truth,
like the connector manifest). Each runner self-records an ``IngestionJob`` (its
``kind``) and is best-effort per ticker, so the orchestrator just dispatches.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# --- runners (thin adapters over the existing/new ingest functions) -------
# Every runner takes ``mode`` ("full" | "delta"). Pipelines that are inherently incremental
# (prices/corp_actions fetch since the last stored bar; news is latest-N) ignore it; the
# item-based RAG pipelines and the financials backfill use it to skip unchanged work.
async def _run_financials(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.jobs import run_backfill
    await run_backfill(market=market, tickers=tickers, deep=True, mode=mode)


async def _run_prices(market: str, tickers: list[str], mode: str = "full") -> None:
    # inherently incremental: run_prices_ingest fetches since the last stored bar per ticker
    from app.config import settings
    from app.store.prices_ingest import history_universe_symbols, run_prices_ingest

    # HL-1: every US prices sweep also refreshes the History Lab anchor universe (^GSPC, ^VIX,
    # ^KS11 … — all Yahoo-global symbols living in the US namespace). First ingest deep-backfills
    # to max history inside run_prices_ingest; afterwards it's the same cheap incremental fetch.
    if market.upper() == "US":
        seen = {t.upper() for t in tickers}
        tickers = list(tickers) + [s for s in sorted(history_universe_symbols()) if s not in seen]
    await run_prices_ingest(market, tickers, years=settings.prices_backfill_years)
    if market.upper() == "US":
        # HL-2: re-derive drawdown episodes for the anchors while their bars are fresh (idempotent;
        # cheap — pure function of the closes) + keep the curated regimes seeded.
        from app.store.history import recompute_episodes, seed_regimes
        for s in sorted(history_universe_symbols()):
            try:
                recompute_episodes("US", s)
            except Exception as exc:  # noqa: BLE001 — one anchor never sinks the sweep
                logger.warning("episode recompute failed for %s: %s", s, exc)
        try:
            seed_regimes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("regime seeding failed: %s", exc)


async def _run_corp_actions(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.corp_actions_ingest import run_corp_actions_ingest
    await run_corp_actions_ingest(market, tickers)   # inherently incremental


async def _run_news(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.news_ingest import run_news_ingest
    await run_news_ingest(market=market, tickers=tickers)   # latest-N — inherently incremental


async def _run_filing_text(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.filing_ingest import run_filing_text_ingest
    await run_filing_text_ingest(market, tickers, mode=mode)


async def _run_transcript_text(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.transcript_ingest import run_transcript_text_ingest
    await run_transcript_text_ingest(market, tickers, mode=mode)


async def _run_presentation_text(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.deck_ingest import run_presentation_text_ingest
    await run_presentation_text_ingest(market, tickers, mode=mode)


async def _run_kr_earnings(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.kr_earnings_ingest import run_kr_earnings_ingest
    await run_kr_earnings_ingest(market, tickers, mode=mode)


async def _run_era_news(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.store.era_news_ingest import run_era_news_ingest
    await run_era_news_ingest(market, tickers)   # tickers = regime slugs (empty = all)


async def _run_logos(market: str, tickers: list[str], mode: str = "full") -> None:
    from app.routers.logos import run_logo_ingest
    await run_logo_ingest(market, tickers)   # cache-first by design (misses only)


# pipeline cadence tiers — the scheduler skips a pipeline that ran within `min_interval_seconds`,
# so heavy historical pulls don't re-fetch the full history every sweep. Pairs with incremental
# fetch in the runners (prices/corp_actions only pull since the last stored date).
_HOUR, _DAY, _WEEK = 3600, 86400, 604800

# id → metadata + runner. `kind` is the IngestionJob kind the runner writes (so the admin
# can group jobs by pipeline). `default` = part of the standard scheduled set.
# `upstream` = the EXACT external API(s) + request each pipeline issues (so the admin can show
# operators "어떤 API를 어떤 쿼리로 fetch하는지" verbatim). `fetch` = scope + re-fetch behavior.
# `min_interval_seconds` = cadence tier (how often the scheduler re-runs this pipeline).
PIPELINES: list[dict] = [
    {"id": "financials", "label": "재무제표", "source": "SEC EDGAR · OpenDART", "store": "financial_facts",
     "delta": "저장된 최신 분기가 아직 신선한 종목은 건너뛰고, 새 보고서가 나왔을 종목만 재수집",
     "kind": "backfill", "markets": ["US", "KR"], "default": True, "runner": _run_financials,
     "min_interval_seconds": _WEEK,
     "desc": "3대 재무제표 + 회사 정보(딥 백필)",
     "upstream": [
         "US · SEC EDGAR (XBRL) — GET https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
         "US · SEC EDGAR (필링 목록) — GET https://data.sec.gov/submissions/CIK{cik}.json",
         "KR · OpenDART — GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
         "?corp_code={corp}&bsns_year={YYYY}&reprt_code={11011|11012|11013|11014}&fs_div=CFS",
     ],
     "fetch": "US: 전 기간 연·분기 XBRL 재무사실 / KR: 최근 15개 보고서(연·분기). UPSERT 키 "
              "(market,ticker,statement,line_item,period,report_period,accession). 전체 모드는 매 실행 전체 재수집; 델타 모드는 저장분이 신선한 종목을 스킵."},
    {"id": "prices", "label": "가격(OHLCV)", "source": "Yahoo Finance", "store": "price_bars",
     "delta": "항상 증분 — 마지막 저장 봉 이후만 fetch (모드 무관)",
     "kind": "prices", "markets": ["US", "KR"], "default": True, "runner": _run_prices,
     "min_interval_seconds": _DAY,
     "desc": "일별 시·고·저·종가 + 거래량",
     "upstream": [
         "US·KR · Yahoo Finance chart — GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
         "?period1={start_epoch}&period2={end_epoch}&interval=1d",
     ],
     "fetch": "일봉 OHLCV+거래량. UPSERT 키 (market,ticker,interval,bar_date). "
              "✅ 증분: 종목별 마지막 저장일 이후만 fetch(최초 1회만 PRICES_BACKFILL_YEARS년 전체)."},
    {"id": "corp_actions", "label": "배당·분할", "source": "Yahoo Finance", "store": "corporate_actions",
     "delta": "항상 증분 — 마지막 이벤트 이후만 fetch (모드 무관)",
     "kind": "corp_actions", "markets": ["US", "KR"], "default": True, "runner": _run_corp_actions,
     "min_interval_seconds": _WEEK,
     "desc": "배당락일·금액 + 액면분할(10년)",
     "upstream": [
         "US·KR · Yahoo Finance chart events — GET https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
         "?period1={start_epoch}&period2={end_epoch}&interval=1d&events=div,split",
     ],
     "fetch": "배당락일·금액 + 액면분할 비율. UPSERT 키 (market,ticker,kind,event_date). "
              "✅ 증분: 종목별 마지막 이벤트일 이후만 fetch(최초 1회만 10년 전체)."},
    {"id": "news", "label": "뉴스 → RAG", "source": "Google News", "store": "RAG corpus",
     "delta": "항상 최신 N건만 — 본질적으로 증분 (모드 무관)",
     "kind": "news", "markets": ["US", "KR"], "default": True, "runner": _run_news,
     "min_interval_seconds": _HOUR,
     "desc": "종목별 최신 헤드라인을 RAG 색인",
     "upstream": [
         "US·KR · Google News RSS — GET https://news.google.com/rss/search"
         "?q={회사명 또는 티커}&hl={ko|en-US}&gl={KR|US}&ceid={KR:ko|US:en}",
     ],
     "fetch": "종목별 최신 헤드라인 NEWS_INGEST_LIMIT건(기본 8) → RAG 색인(doc_id=url). "
              "과거 이력 없음 — 최신 N건만 반환(본질적으로 증분)."},
    {"id": "filing_text", "label": "공시 본문 → RAG", "source": "SEC iXBRL · OpenDART", "store": "RAG corpus",
     "delta": "이미 색인한 접수번호는 건너뛰고 새 공시만 다운로드·임베딩 (쿼터 절약)",
     "kind": "filing_text", "markets": ["US", "KR"], "default": True, "runner": _run_filing_text,
     "min_interval_seconds": _WEEK,
     "desc": "공시 본문 HTML을 텍스트 추출해 RAG 색인(인앱 뷰어와 동일 원천)",
     "upstream": [
         "US · SEC iXBRL 본문 — GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}",
         "KR · OpenDART document.xml — GET https://opendart.fss.or.kr/api/document.xml?rcept_no={rcept_no}",
     ],
     "fetch": "재무제표에 등장한 최근 4개 공시 본문 HTML을 텍스트 추출→RAG 색인(doc_id={accession}:s.{n}). "
              "HTML은 인앱 뷰어와 동일 원천을 공유·캐시(증분)."},
    {"id": "transcript_text", "label": "어닝콜 트랜스크립트 → RAG", "source": "API Ninjas", "store": "RAG corpus",
     "delta": "이미 색인한 분기는 건너뛰고 새 분기만",
     "kind": "transcript", "markets": ["US", "KR"], "default": False, "runner": _run_transcript_text,
     "min_interval_seconds": _WEEK,
     "desc": "분기 어닝콜 전문(화자별)을 RAG 색인 — 인앱 트랜스크립트 프리뷰와 동일 원천 (US+KR, API_NINJAS_KEY 필수)",
     "upstream": [
         "US·KR · API Ninjas 어닝콜 전문 — GET https://api.api-ninjas.com/v1/earningstranscript"
         "?ticker={SYM|005930.KS}&year={YYYY}&quarter={n} (X-Api-Key, 프리미엄 · ~5년 깊이)",
     ],
     "fetch": "최근 TRANSCRIPT_INGEST_LIMIT개 분기(기본 8) 어닝콜 전문을 화자별 텍스트로 RAG 색인 "
              "(doc_id=TR:{ticker}:{quarter}:s.{n}). KR 코드는 .KS→.KQ로 시도. API_NINJAS_KEY 없으면 dark."},
    {"id": "presentation_text", "label": "어닝 발표자료(8-K 덱) → RAG", "source": "SEC EDGAR · Document AI",
     "delta": "이미 파싱한 덱은 건너뛰고 새 덱만 (Document AI 비용 절약)",
     "kind": "presentation", "markets": ["US"], "default": False, "runner": _run_presentation_text,
     "min_interval_seconds": _WEEK,
     "desc": "8-K EX-99 투자자/실적 발표 슬라이드(PDF)를 Document AI로 파싱→RAG 색인 + 인앱 pdf.js 프리뷰 (US, GCP 필요)",
     "upstream": [
         "US · SEC EDGAR 8-K 인덱스 — GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json",
         "US · 발표자료 PDF — GET https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{ex99}.pdf",
         "GCP · Document AI Layout Parser — processDocument(processor={DOCAI_PROCESSOR_ID}, pdf)",
     ],
     "fetch": "최근 DECK_INGEST_LIMIT개 8-K EX-99 발표자료(PDF)를 Document AI Layout Parser로 충실 파싱 "
              "(페이지·좌표 포함)→RAG 색인(doc_id=DECK:{ticker}:{accession}:c{n}). PDF는 캐시→pdf.js 뷰어가 동일 원천 서빙."},
    {"id": "kr_earnings", "label": "실적공시(잠정실적 공정공시) → RAG", "source": "OpenDART", "store": "RAG corpus",
     "delta": "이미 색인한 공시는 건너뛰고 새 잠정실적만 (쿼터 절약)",
     "kind": "kr_earnings", "markets": ["KR"], "default": False, "runner": _run_kr_earnings,
     "min_interval_seconds": _WEEK,
     "desc": "KR 어닝 등가물 — '영업(잠정)실적(공정공시)' 본문을 RAG 색인 + 인앱 DART 뷰어 동일 원천 (KR, 무료 API 없는 트랜스크립트/덱 대신)",
     "upstream": [
         "KR · OpenDART 공시목록 — GET https://opendart.fss.or.kr/api/list.json"
         "?corp_code={corp}&bgn_de=…&end_de=… (report_nm⊇'실적'&'공정공시' 필터)",
         "KR · OpenDART document.xml — GET https://opendart.fss.or.kr/api/document.xml?rcept_no={rcept_no}",
     ],
     "fetch": "최근 KR_EARNINGS_INGEST_LIMIT개(기본 4) 잠정실적 공정공시 본문 HTML을 텍스트 추출→RAG 색인 "
              "(doc_id={rcept_no}:s.{n}, doc_type=earnings). HTML은 인앱 DART 뷰어와 동일 원천 공유·캐시. "
              "US는 no-op(어닝콜 트랜스크립트 파이프라인 사용)."},
    {"id": "era_news", "label": "시대 뉴스 → RAG", "source": "GDELT · NYT Archive", "store": "RAG corpus",
     "delta": "국면 단위 재색인 — 델타 구분 없음",
     "kind": "era_news", "markets": ["US"], "default": False, "runner": _run_era_news,
     "min_interval_seconds": _WEEK,
     "desc": "큐레이션된 역사적 국면(닷컴버블·GFC·코로나·IMF 등)의 구간 뉴스를 RAG 색인(doc_type=era_news) — "
             "2017+는 GDELT(무료), 이전은 NYT Archive(NYT_API_KEY 필요, 없으면 갭). ticker=국면 slug로 검색 범위.",
     "upstream": [
         "US · GDELT DOC 2.0 artlist — GET https://api.gdeltproject.org/api/v2/doc/doc?mode=artlist (키 불필요, 2017+)",
         "US · NYT Archive — GET https://api.nytimes.com/svc/archive/v1/{year}/{month}.json?api-key=… (1851+)",
     ],
     "fetch": "각 국면 구간(start~end)의 위기 관련 기사(제목·초록)를 최대 60건 RAG 색인 "
              "(doc_id=era:{slug}:{url}). 커버리지 없는 구간(2017 이전+NYT 키 없음)은 0 chunks(갭)."},
]

PIPELINES.append(
    {"id": "logos", "label": "회사 로고", "source": "Logo.dev / FMP / favicon", "store": "logos(volume)",
     "delta": "항상 캐시 미스만 재시도 (모드 무관)",
     "kind": "logo", "markets": ["US", "KR"], "default": False, "runner": _run_logos,
     "min_interval_seconds": _WEEK,
     "desc": "종목 로고 이미지(하이브리드 해석·캐시). 없으면 UI가 모노그램 표시(무 날조).",
     "upstream": [
         "Logo.dev — GET https://img.logo.dev/ticker/{SYMBOL} 또는 /{domain} (LOGODEV_TOKEN 있을 때)",
         "FMP — GET https://financialmodelingprep.com/stable/profile?symbol={SYMBOL} (image/website)",
         "Google favicon — GET https://www.google.com/s2/favicons?domain={domain}&sz=128 (도메인 있을 때만)",
     ],
     "fetch": "티커별 로고 1장을 해석→/data/logos에 캐시(.img+.meta), 미스는 .miss 마커. 재실행 시 "
              "기존 캐시는 건너뛰고 미스만 재시도."})

PIPELINE_BY_ID = {p["id"]: p for p in PIPELINES}
KIND_TO_PIPELINE = {p["kind"]: p for p in PIPELINES}


def list_pipelines() -> list[dict]:
    """Pipeline metadata (no runner) for the admin/scheduler views."""
    return [{k: v for k, v in p.items() if k != "runner"} for p in PIPELINES]


def default_pipeline_ids() -> list[str]:
    return [p["id"] for p in PIPELINES if p["default"]]


def resolve_pipeline_ids(ids: list[str] | None) -> list[str]:
    """Validate requested ids against the registry; fall back to the default set."""
    if not ids:
        return default_pipeline_ids()
    valid = [i for i in ids if i in PIPELINE_BY_ID]
    return valid or default_pipeline_ids()


async def run_pipelines(market: str, tickers: list[str], pipeline_ids: list[str] | None = None,
                        mode: str = "full") -> dict:
    """Run the selected pipelines over one (market, tickers) set. Each runner self-records its
    IngestionJob and is best-effort; we only catch a hard runner crash so one pipeline never
    sinks the rest. ``mode="delta"`` = only new/changed items (see each pipeline's `delta` note).
    Returns {pipeline_id: 'ok' | 'skipped' | 'error: …'}."""
    ids = resolve_pipeline_ids(pipeline_ids)
    summary: dict[str, str] = {}
    for p in PIPELINES:
        if p["id"] not in ids:
            continue
        if market not in p["markets"]:
            summary[p["id"]] = "skipped"
            continue
        try:
            await p["runner"](market, tickers, mode=mode)
            summary[p["id"]] = "ok"
        except Exception as exc:  # noqa: BLE001 — one pipeline failing never sinks the others
            logger.warning("pipeline %s failed for %s: %s", p["id"], market, exc)
            summary[p["id"]] = f"error: {exc}"
    return summary
