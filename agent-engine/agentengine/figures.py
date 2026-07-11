"""Pull the specific figures out of a structured tool result: number formatting,
per-statement column specs, the small extracted preview table, and the as-of date.

Extracted from ``citations.py`` so the "turn a known datasets/RAG result shape into
a snippet + a compact table of the real numbers" concern lives apart from the
citation-assembly logic. ``citations.py`` re-exports these (agent.py references them).
"""

from __future__ import annotations

import re

from agentengine.provenance import _PROV_KEYS


def _fmt_ratio(v) -> str:
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def _fmt_amt(v) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:.2f}T"
    if a >= 1e9:
        return f"{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    return f"{v:,.0f}" if a >= 1 else f"{v:.4f}"


# known result shapes → (snippet, table[header-first]) showing the SPECIFIC figures used.
_METRIC_COLS = (("gross_margin", "매출총이익률", _fmt_ratio), ("operating_margin", "영업이익률", _fmt_ratio),
                ("net_margin", "순이익률", _fmt_ratio), ("return_on_equity", "ROE", _fmt_ratio))
_INCOME_COLS = (("revenue", "매출", _fmt_amt), ("operating_income", "영업이익", _fmt_amt),
                ("net_income", "순이익", _fmt_amt))
_BALANCE_COLS = (("total_assets", "자산총계", _fmt_amt), ("total_liabilities", "부채총계", _fmt_amt),
                 ("shareholders_equity", "자본총계", _fmt_amt))
_CASHFLOW_COLS = (("net_cash_flow_from_operations", "영업활동CF", _fmt_amt),
                  ("net_cash_flow_from_investing", "투자활동CF", _fmt_amt),
                  ("net_cash_flow_from_financing", "재무활동CF", _fmt_amt))


def _shape_table(rows: list[dict], period_key: str, cols, period_label: str):
    rows = [r for r in rows if isinstance(r, dict)][:6]
    use = [(k, lbl, fn) for k, lbl, fn in cols if any(r.get(k) is not None for r in rows)]
    if not rows or not use:
        return None, None
    header = [period_label] + [lbl for _, lbl, _ in use]
    table = [header]
    for r in rows:
        table.append([str(r.get(period_key) or "—")] + [fn(r.get(k)) for k, _, fn in use])
    top = rows[0]
    snippet = " · ".join(f"{lbl} {fn(top.get(k))}" for k, lbl, fn in use if top.get(k) is not None)
    if top.get(period_key):
        snippet += f" ({top.get(period_key)})"
    return snippet or None, table


def _statements_root(data):
    """`/financials` nests the three statements under a top-level ``financials`` wrapper
    ({"financials": {"income_statements": […], …}}) while the per-statement routes return them
    at the top level — normalize so every extractor sees one shape. Without this the combined
    endpoint produced citations with NO table/snippet/evidence anchor (the 재무제표 preview
    regression)."""
    if isinstance(data, dict) and isinstance(data.get("financials"), dict):
        return data["financials"]
    return data


def _evidence(tool: dict, data) -> tuple[str | None, list[list[str]] | None]:
    """The specific figures a structured result contributed — a one-line computation
    summary + a small extracted table — so the preview shows real data, not a label."""
    if not isinstance(data, dict):
        return None, None
    data = _statements_root(data)
    if isinstance(data.get("metrics"), list):
        return _shape_table(data["metrics"], "report_period", _METRIC_COLS, "기간")
    if isinstance(data.get("income_statements"), list):
        return _shape_table(data["income_statements"], "report_period", _INCOME_COLS, "기간")
    if isinstance(data.get("balance_sheets"), list):
        return _shape_table(data["balance_sheets"], "report_period", _BALANCE_COLS, "기간")
    if isinstance(data.get("cash_flow_statements"), list):
        return _shape_table(data["cash_flow_statements"], "report_period", _CASHFLOW_COLS, "기간")
    if isinstance(data.get("interest_rates"), list):
        rows = sorted([r for r in data["interest_rates"] if isinstance(r, dict)],
                      key=lambda r: str(r.get("date") or ""), reverse=True)
        rr = [r for r in rows if r.get("rate") is not None][:6]
        if rr:
            table = [["기관", "금리", "기준일"]] + [
                [str(r.get("name") or r.get("bank") or "—"), f"{r.get('rate')}%", str(r.get("date") or "—")[:10]]
                for r in rr]
            top = rr[0]
            return f"{top.get('name') or top.get('bank')} {top.get('rate')}% ({str(top.get('date'))[:10]})", table
    if isinstance(data.get("dividends"), list) and data["dividends"]:
        rows = [r for r in data["dividends"] if isinstance(r, dict) and r.get("amount") is not None][:6]
        if rows:
            table = [["배당락일", "배당금"]] + [[str(r.get("ex_date")), _fmt_amt(r.get("amount"))] for r in rows]
            top = rows[0]
            return f"배당 {_fmt_amt(top.get('amount'))} ({top.get('ex_date')})", table
    if isinstance(data.get("observations"), list) and data.get("name"):  # economic indicator (PH-DATA-4)
        rows = [r for r in data["observations"] if isinstance(r, dict) and r.get("value") is not None][-6:][::-1]
        if rows:
            pct = data.get("unit") == "%"
            def _v(x):
                return f"{x:g}%" if pct else (_fmt_amt(x) if abs(x) >= 1000 else f"{x:g}")
            table = [["기간", str(data["name"])]] + [[str(r.get("date")), _v(r.get("value"))] for r in rows]
            top = rows[0]
            return f"{data['name']} {_v(top.get('value'))} ({top.get('date')})", table
    if isinstance(data.get("indicators"), list) and data["indicators"] and \
            isinstance(data["indicators"][0], dict) and "lines" in data["indicators"][0]:  # technical (PH-DATA-6)
        table = [["지표", "최신값"]]
        for ind in data["indicators"]:
            for ln in (ind.get("lines") or []):
                if ln.get("latest") is not None:
                    lbl = ln.get("label") or ind.get("name")
                    pct = ind.get("unit") == "percent"
                    val = f"{ln['latest']:g}%" if pct else (_fmt_amt(ln["latest"]) if abs(ln["latest"]) >= 1000 else f"{ln['latest']:g}")
                    table.append([str(lbl), val])
        if len(table) > 1:
            head = table[1]
            return f"{head[0]} {head[1]} (as of {data.get('as_of')}) · 서술적 지표(신호 아님)", table
    if isinstance(data.get("prices"), list):
        rows = [r for r in data["prices"] if isinstance(r, dict)]
        rows = sorted(rows, key=lambda r: str(r.get("time") or ""), reverse=True)[:6]
        pr = [r for r in rows if r.get("close") is not None]
        if pr:
            table = [["날짜", "종가"]] + [[str(r.get("time"))[:10], _fmt_amt(r.get("close"))] for r in pr]
            top = pr[0]
            return f"종가 {_fmt_amt(top.get('close'))} ({str(top.get('time'))[:10]})", table
    # generic fallback: first list-of-dicts → a compact table of its real values
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            rows = [r for r in v if isinstance(r, dict)][:6]
            keys = [k for k in rows[0] if k.lower() not in _PROV_KEYS
                    and isinstance(rows[0].get(k), (int, float, str))][:4]
            if not keys:
                break
            header = keys
            table = [header] + [[_fmt_amt(r.get(k)) if isinstance(r.get(k), (int, float)) else str(r.get(k) or "—")
                                 for k in keys] for r in rows]
            snippet = " · ".join(f"{k} {table[1][i]}" for i, k in enumerate(keys))
            return snippet or None, table
    return None, None


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _collect_dates(obj, out: list) -> None:
    """Gather YYYY-MM-DD values under date-ish keys (report_period / as_of / date / filing_date)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and _DATE_RE.match(v) and any(t in k.lower() for t in ("date", "period", "as_of")):
                out.append(v[:10])
            else:
                _collect_dates(v, out)
    elif isinstance(obj, list):
        for x in obj[:50]:
            _collect_dates(x, out)


def _latest_date(data) -> str | None:
    """Most recent date-ish value in a datasets response — the figure's as-of."""
    found: list[str] = []
    _collect_dates(data, found)
    return max(found) if found else None
