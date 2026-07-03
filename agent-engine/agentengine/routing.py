"""Ticker resolution for the Gemini path.

``resolve_ticker`` normalizes the ticker the Gemini planner produces (e.g. "Apple" → "AAPL", a
KR name → its 6-digit code) and ``_user_text`` flattens a conversation's user turns for cross-turn
context. Both are live on the Gemini path and re-exported via ``planner.py``. Pure data + functions
(no LLM, no I/O).

(The platform is Gemini-only — invariant #7. The legacy keyword-routing / arg-builder / summary
helpers from the removed stub planner were deleted in RF-01; routing/clarification/synthesis are all
LLM-judged now.)"""

from __future__ import annotations

import re

# Well-known names -> ticker, so free-form questions resolve without an explicit symbol.
# 6-digit codes => KR market; alphabetic => US.
_NAME_TO_TICKER: dict[str, str] = {
    # --- KR (KRX 6-digit) ---
    "삼성전자": "005930", "samsung electronics": "005930", "sk하이닉스": "000660", "하이닉스": "000660",
    "네이버": "035420", "naver": "035420", "카카오": "035720", "kakao": "035720",
    "현대차": "005380", "현대자동차": "005380", "기아": "000270", "lg에너지솔루션": "373220",
    "포스코": "005490", "posco": "005490", "셀트리온": "068270", "삼성바이오로직스": "207940",
    "kb금융": "105560", "신한지주": "055550", "lg화학": "051910", "삼성sdi": "006400",
    # --- US ---
    "apple": "AAPL", "애플": "AAPL", "nvidia": "NVDA", "엔비디아": "NVDA", "tesla": "TSLA", "테슬라": "TSLA",
    "microsoft": "MSFT", "마이크로소프트": "MSFT", "amazon": "AMZN", "아마존": "AMZN",
    "google": "GOOGL", "alphabet": "GOOGL", "구글": "GOOGL", "meta": "META", "메타": "META",
    "intel": "INTC", "인텔": "INTC", "amd": "AMD", "tsmc": "TSM", "netflix": "NFLX", "넷플릭스": "NFLX",
}
_TICKER = re.compile(r"\b([A-Z]{1,5})\b")
_KRCODE = re.compile(r"\b(\d{6})\b")
# Finance acronyms / units that look like tickers but aren't.
_STOP = {
    "US", "KR", "I", "A", "AN", "THE", "SEC", "CEO", "CFO", "COO", "Q", "FY", "AI", "ETF", "GPU", "CPU",
    "EPS", "PER", "PBR", "PSR", "ROE", "ROA", "ROIC", "EBIT", "EBITDA", "COGS", "CAPEX", "OPEX", "FCF",
    "GDP", "CPI", "PPI", "PMI", "USD", "KRW", "JPY", "EUR", "CNY", "IPO", "NAV", "YOY", "QOQ", "TTM",
    "FED", "ECB", "BOJ", "BOE", "BOK", "IR", "PR", "ESG", "API",
}


def resolve_ticker(task: str) -> str | None:
    low = task.lower()
    # 1) known company name (longest match first so "삼성전자" beats "삼성"). ASCII
    #    names need word boundaries so "intel" doesn't fire on "intelligence".
    for name in sorted(_NAME_TO_TICKER, key=len, reverse=True):
        if name.isascii():
            if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", low):
                return _NAME_TO_TICKER[name]
        elif name in low:
            return _NAME_TO_TICKER[name]
    # 2) explicit KR 6-digit code
    kr = _KRCODE.search(task)
    if kr:
        return kr.group(1)
    # 3) an explicit uppercase symbol that isn't a finance acronym
    for tok in _TICKER.findall(task):
        if tok not in _STOP:
            return tok
    return None


def _user_text(conversation: list | None) -> str:
    """Concatenate the user turns of a conversation (for cross-turn context)."""
    return " ".join(
        m.get("content", "") for m in (conversation or [])
        if m.get("role") == "user" and m.get("content")
    )
