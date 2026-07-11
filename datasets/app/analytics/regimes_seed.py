"""Curated market-regime reference data (HL-2) — named historical episodes with their own sources.

This is reference DATA, not logic (ROADMAP §2.3): the narrative window (start/end) and metadata are
curated + cited here; the exact peak/trough depths come from the DERIVED ``DrawdownEpisode`` rows and
are cross-checked against ``peak_hint``/``trough_hint`` at load (mismatch → warn, never overwrite).

`kind` ∈ bubble | crisis | bear | rate_cycle | recovery. Dates are ISO strings (parsed at load).
Every regime cites ≥1 source.
"""

from __future__ import annotations

_FRH = "Federal Reserve History"
_NBER = "National Bureau of Economic Research"
_BOK = "Bank of Korea"
_IMF = "International Monetary Fund"


REGIMES: list[dict] = [
    {
        "slug": "black-monday-1987", "name_kr": "블랙 먼데이", "name_en": "Black Monday 1987",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "crisis",
        "start_date": "1987-08-25", "end_date": "1987-12-04",
        "peak_hint": "1987-08-25", "trough_hint": "1987-12-04",
        "description": "1987년 10월 19일 하루 S&P500이 −20.5% 폭락한 사상 최대 일간 낙폭 사건. "
                       "포트폴리오 보험·프로그램 매매가 낙폭을 증폭시켰다.",
        "sources": [{"title": "Stock Market Crash of 1987", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/essays/stock-market-crash-of-1987"}],
    },
    {
        "slug": "gulf-war-1990", "name_kr": "걸프전 약세장", "name_en": "Gulf War Bear 1990",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "bear",
        "start_date": "1990-07-16", "end_date": "1990-10-11",
        "peak_hint": "1990-07-16", "trough_hint": "1990-10-11",
        "description": "이라크의 쿠웨이트 침공에 따른 유가 급등과 경기침체 우려로 촉발된 약세장.",
        "sources": [{"title": "US Business Cycle Expansions and Contractions", "publisher": _NBER,
                     "url": "https://www.nber.org/research/data/us-business-cycle-expansions-and-contractions"}],
    },
    {
        "slug": "dotcom-bust", "name_kr": "닷컴 버블 붕괴", "name_en": "Dot-com Bust",
        "market": "US", "anchor_ticker": "^IXIC", "kind": "bubble",
        "start_date": "2000-03-10", "end_date": "2002-10-09",
        "peak_hint": "2000-03-10", "trough_hint": "2002-10-09",
        "description": "인터넷 기업 밸류에이션 거품 붕괴로 나스닥이 고점 대비 약 −78% 하락했다.",
        "sources": [{"title": "Dot-com bubble", "publisher": _NBER,
                     "url": "https://www.nber.org/research/data/us-business-cycle-expansions-and-contractions"}],
    },
    {
        "slug": "sept-11-2001", "name_kr": "9·11 충격", "name_en": "September 11 Shock",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "crisis",
        "start_date": "2001-09-10", "end_date": "2001-09-21",
        "peak_hint": "2001-09-10", "trough_hint": "2001-09-21",
        "description": "9·11 테러로 미국 증시가 나흘간 휴장했다가 재개장 후 급락한 단기 충격.",
        "sources": [{"title": "September 11 and the Markets", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/"}],
    },
    {
        "slug": "gfc-2008", "name_kr": "글로벌 금융위기", "name_en": "Global Financial Crisis",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "crisis",
        "start_date": "2007-10-09", "end_date": "2009-03-09",
        "peak_hint": "2007-10-09", "trough_hint": "2009-03-09",
        "description": "서브프라임 모기지 부실과 리먼 브라더스 파산으로 S&P500이 고점 대비 약 −57% 하락했다.",
        "sources": [{"title": "The Great Recession of 2007–09", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/essays/great-recession-of-200709"}],
    },
    {
        "slug": "flash-crash-2010", "name_kr": "플래시 크래시", "name_en": "Flash Crash 2010",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "crisis",
        "start_date": "2010-04-23", "end_date": "2010-07-02",
        "peak_hint": "2010-04-23", "trough_hint": "2010-07-02",
        "description": "2010년 5월 6일 장중 급락(플래시 크래시)과 유럽 재정 우려가 겹친 조정.",
        "sources": [{"title": "US Business Cycle Reference Dates", "publisher": _NBER,
                     "url": "https://www.nber.org/research/data/us-business-cycle-expansions-and-contractions"}],
    },
    {
        "slug": "euro-crisis-2011", "name_kr": "유럽 재정위기", "name_en": "Euro Debt Crisis 2011",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "bear",
        "start_date": "2011-04-29", "end_date": "2011-10-03",
        "peak_hint": "2011-04-29", "trough_hint": "2011-10-03",
        "description": "그리스 등 유로존 국가부채 위기와 미국 신용등급 강등으로 촉발된 조정.",
        "sources": [{"title": "European Sovereign Debt Crisis", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/"}],
    },
    {
        "slug": "taper-tantrum-2013", "name_kr": "테이퍼 탠트럼", "name_en": "Taper Tantrum 2013",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "rate_cycle",
        "start_date": "2013-05-21", "end_date": "2013-06-24",
        "peak_hint": "2013-05-21", "trough_hint": "2013-06-24",
        "description": "연준의 자산매입 축소(테이퍼링) 시사에 금리가 급등하며 나타난 단기 조정.",
        "sources": [{"title": "Taper Tantrum", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/"}],
    },
    {
        "slug": "vol-spike-2018", "name_kr": "2018 변동성 쇼크", "name_en": "2018 Volatility Spike",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "bear",
        "start_date": "2018-09-20", "end_date": "2018-12-24",
        "peak_hint": "2018-09-20", "trough_hint": "2018-12-24",
        "description": "연준 긴축과 무역분쟁 우려로 4분기 S&P500이 고점 대비 약 −20% 하락했다.",
        "sources": [{"title": "US Business Cycle Reference Dates", "publisher": _NBER,
                     "url": "https://www.nber.org/research/data/us-business-cycle-expansions-and-contractions"}],
    },
    {
        "slug": "covid-crash-2020", "name_kr": "코로나 폭락", "name_en": "COVID-19 Crash",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "crisis",
        "start_date": "2020-02-19", "end_date": "2020-03-23",
        "peak_hint": "2020-02-19", "trough_hint": "2020-03-23",
        "description": "코로나19 팬데믹으로 S&P500이 약 한 달 만에 고점 대비 약 −34% 폭락했다.",
        "sources": [{"title": "US Business Cycle Peak February 2020", "publisher": _NBER,
                     "url": "https://www.nber.org/news/business-cycle-dating-committee-announcement-june-8-2020"}],
    },
    {
        "slug": "inflation-bear-2022", "name_kr": "2022 인플레 약세장", "name_en": "2022 Inflation Bear",
        "market": "US", "anchor_ticker": "^GSPC", "kind": "rate_cycle",
        "start_date": "2022-01-03", "end_date": "2022-10-12",
        "peak_hint": "2022-01-03", "trough_hint": "2022-10-12",
        "description": "40년래 최고 인플레이션에 맞선 연준의 급격한 금리인상으로 S&P500이 약 −25% 하락했다.",
        "sources": [{"title": "Monetary Policy 2022", "publisher": _FRH,
                     "url": "https://www.federalreservehistory.org/"}],
    },
    {
        "slug": "kr-imf-1997", "name_kr": "IMF 외환위기", "name_en": "Korea IMF Crisis 1997",
        "market": "KR", "anchor_ticker": "^KS11", "kind": "crisis",
        "start_date": "1997-06-01", "end_date": "1998-06-16",
        "peak_hint": "1997-06-01", "trough_hint": "1998-06-16",
        "description": "아시아 외환위기로 원화가 급락하고 IMF 구제금융을 받았으며 코스피가 폭락했다. "
                       "(야후 지수 데이터는 1997년경부터 시작 — 그 이전은 갭으로 표시.)",
        "sources": [{"title": "Republic of Korea: IMF Stand-By Arrangement 1997", "publisher": _IMF,
                     "url": "https://www.imf.org/external/country/kor/"}],
    },
    {
        "slug": "kr-card-crisis-2003", "name_kr": "카드사태", "name_en": "Korea Card Crisis 2003",
        "market": "KR", "anchor_ticker": "^KS11", "kind": "crisis",
        "start_date": "2002-04-18", "end_date": "2003-03-17",
        "peak_hint": "2002-04-18", "trough_hint": "2003-03-17",
        "description": "신용카드 과다발급에 따른 가계부채 부실로 촉발된 신용경색과 증시 조정.",
        "sources": [{"title": "Financial Stability Report", "publisher": _BOK,
                     "url": "https://www.bok.or.kr/eng/main/main.do"}],
    },
    {
        "slug": "kr-gfc-2008", "name_kr": "2008 금융위기(한국)", "name_en": "Korea GFC 2008",
        "market": "KR", "anchor_ticker": "^KS11", "kind": "crisis",
        "start_date": "2007-10-31", "end_date": "2008-10-24",
        "peak_hint": "2007-10-31", "trough_hint": "2008-10-24",
        "description": "글로벌 금융위기로 외국인 자금이 대거 이탈하며 코스피가 고점 대비 약 −54% 하락했다.",
        "sources": [{"title": "Financial Stability Report 2008", "publisher": _BOK,
                     "url": "https://www.bok.or.kr/eng/main/main.do"}],
    },
    {
        "slug": "kr-covid-2020", "name_kr": "코로나 폭락(한국)", "name_en": "Korea COVID Crash 2020",
        "market": "KR", "anchor_ticker": "^KS11", "kind": "crisis",
        "start_date": "2020-01-22", "end_date": "2020-03-19",
        "peak_hint": "2020-01-22", "trough_hint": "2020-03-19",
        "description": "코로나19 팬데믹으로 코스피가 한 달여 만에 고점 대비 약 −35% 폭락했다.",
        "sources": [{"title": "Economic Outlook 2020", "publisher": _BOK,
                     "url": "https://www.bok.or.kr/eng/main/main.do"}],
    },
]


def regimes_for(market: str | None = None) -> list[dict]:
    """Seed regimes, optionally filtered by market (US | KR); GLOBAL/None returns all."""
    if not market:
        return list(REGIMES)
    m = market.upper()
    return [r for r in REGIMES if r["market"] == m]
