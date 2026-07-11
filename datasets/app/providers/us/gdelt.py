"""Era news via the GDELT DOC 2.0 API (keyless, global news back to 2017).

    https://api.gdeltproject.org/api/v2/doc/doc

Two shapes for History Lab (HL-5):
- ``news_timeline(query, from, to)`` → a daily VOLUME + average TONE time series (chartable
  overlay for a regime window) via ``mode=timelinevolinfo``.
- ``news_search(query, from, to, limit)`` → an article list (title · domain · date · url ·
  tone) via ``mode=artlist``.

Descriptive only — raw coverage volume and tone of the RECORD, never a forecast. GDELT's index
starts 2017, so pre-2017 regimes (dot-com, GFC) fall to NYT Archive; we return an empty series
with a `coverage_note` rather than fabricating (gaps are drawn).
"""

from __future__ import annotations

from datetime import date

from app.http import fetch_json

_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; ValueGraphDatasets/0.1)"}
_GDELT_START = date(2017, 1, 1)  # DOC 2.0 coverage floor


def _stamp(d: date, end: bool = False) -> str:
    return d.strftime("%Y%m%d") + ("235959" if end else "000000")


def _clamp_note(start: date, end: date) -> str | None:
    if end < _GDELT_START:
        return "GDELT 색인은 2017년부터입니다 — 이 구간은 시대 뉴스(NYT Archive) 대상입니다."
    if start < _GDELT_START:
        return "GDELT 색인 시작(2017-01) 이전 구간은 제외되었습니다."
    return None


class GdeltProvider:
    async def news_timeline(self, query: str, start: date, end: date) -> dict:
        note = _clamp_note(start, end)
        eff_start = max(start, _GDELT_START)
        series: list[dict] = []
        if end >= _GDELT_START:
            params = {"query": query, "mode": "timelinevolinfo", "format": "json",
                      "startdatetime": _stamp(eff_start), "enddatetime": _stamp(end, end=True)}
            data = await fetch_json("gdelt", _BASE, params=params, headers=_UA)
            # timelinevolinfo → {"timeline": [{"series": "...", "data": [{"date","value","norm"}]}]}
            tl = (data.get("timeline") or []) if isinstance(data, dict) else []
            vol = next((t for t in tl if "Volume" in str(t.get("series", ""))), tl[0] if tl else {})
            tone = next((t for t in tl if "Tone" in str(t.get("series", ""))), {})
            tone_by_date = {p.get("date"): p.get("value") for p in (tone.get("data") or [])}
            for p in vol.get("data") or []:
                d = p.get("date")
                series.append({"date": _iso(d), "volume": p.get("value"),
                               "tone": tone_by_date.get(d)})
        return {"source": "GDELT DOC 2.0", "query": query, "method": "gdelt-timeline-v1",
                "from": start.isoformat(), "to": end.isoformat(),
                "coverage_note": note, "series": series}

    async def news_search(self, query: str, start: date, end: date, limit: int) -> dict:
        note = _clamp_note(start, end)
        articles: list[dict] = []
        if end >= _GDELT_START:
            params = {"query": query, "mode": "artlist", "format": "json",
                      "maxrecords": max(1, min(limit, 75)), "sort": "datedesc",
                      "startdatetime": _stamp(max(start, _GDELT_START)), "enddatetime": _stamp(end, end=True)}
            data = await fetch_json("gdelt", _BASE, params=params, headers=_UA)
            for a in (data.get("articles") or []) if isinstance(data, dict) else []:
                articles.append({
                    "title": a.get("title"), "url": a.get("url"),
                    "domain": a.get("domain"), "date": _iso_seen(a.get("seendate")),
                    "language": a.get("language"), "tone": a.get("tone"),
                })
        return {"source": "GDELT DOC 2.0", "query": query, "method": "gdelt-artlist-v1",
                "from": start.isoformat(), "to": end.isoformat(),
                "coverage_note": note, "articles": articles}


def _iso(yyyymmddhhmmss) -> str | None:
    s = str(yyyymmddhhmmss or "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


def _iso_seen(seendate) -> str | None:
    # GDELT seendate = "20080915T120000Z"
    s = str(seendate or "").replace("-", "").replace(":", "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None
