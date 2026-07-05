"""M-DERIV (DRV-1) — the data plane embeds a `computation` block AT the computation site.

A derived figure's trust envelope is its math: the formula (with symbols), the sourced
inputs (each carrying its own as_of/accession so it opens in the /evidence viewer), the
assumptions, and the steps to the final value. The agent NEVER reconstructs this — the
block rides the response verbatim (single truth) into the citation (agent DRV-2) and the
Derivation Card renderer (web DRV-3).

Everything here is a plain dict (additive JSON): {method, formula, inputs, assumptions,
steps, note} with rows {label, value, source?, symbol?, evidence?}. Only endpoints where
WE derive the number emit one — values passed through from a source as-is never do.
"""

from __future__ import annotations


def calc_row(label: str, value, source: str | None = None, symbol: str | None = None,
             evidence: dict | None = None) -> dict:
    row: dict = {"label": label, "value": str(value)}
    if source:
        row["source"] = source
    if symbol:
        row["symbol"] = symbol
    if evidence:
        row["evidence"] = evidence
    return row


def computation(method: str, formula: str | None = None, inputs: list[dict] | None = None,
                assumptions: list[dict] | None = None, steps: list[dict] | None = None,
                note: str | None = None) -> dict:
    out: dict = {"method": method}
    if formula:
        out["formula"] = formula
    out["inputs"] = inputs or []
    out["assumptions"] = assumptions or []
    out["steps"] = steps or []
    if note:
        out["note"] = note
    return out


def fmt(v) -> str:
    """Abbreviated numeral for computation traces (mirrors the agent's _money)."""
    if not isinstance(v, (int, float)):
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"{v/1e12:,.2f}T"
    if a >= 1e9:
        return f"{v/1e9:,.2f}B"
    if a >= 1e6:
        return f"{v/1e6:,.1f}M"
    return f"{v:,.2f}"


# --- technical indicators (store/technical.py) ------------------------------------------
# formula per indicator kind — descriptive definitions, not signals.
TECH_FORMULA = {
    "sma": "SMA(n) = 최근 n일 종가 평균",
    "ema": "EMA(n) = 지수가중 이동평균, α = 2/(n+1)",
    "rsi": "RSI(n) = 100 − 100/(1+RS),  RS = n일 평균상승 ÷ 평균하락",
    "macd": "MACD = EMA(12) − EMA(26),  Signal = MACD의 EMA(9)",
    "bbands": "밴드 = SMA(n) ± 2×σ(n일 종가 표준편차)",
    "volatility": "실현변동성(n) = 일수익률 표준편차(n일) × √252 (연율화)",
}


# --- /history/* (routers/history.py) -----------------------------------------------------
# method id → how the statistic is derived. Every entry is descriptive of the RECORD;
# the envelope's "과거 기록 · 전망 아님" label stays mandatory regardless.
HISTORY_FORMULA = {
    "dd-v1": ("낙폭 계산 (dd-v1)", "낙폭ₜ = 종가ₜ ÷ 직전 역대 최고가 − 1"),
    "base-rates-v1": ("조건부 빈도 집계 (base-rates-v1)",
                      "이벤트일 D마다 수익률(D+h) = 종가_{D+h} ÷ 종가_D − 1 → h별 분포 요약 "
                      "(n·중앙값·p25–p75·최악/최고·상승마감비율)"),
    "analogue-v1": ("유사 구간 탐색 (analogue-v1)",
                    "유사도 = 현재 수익률 윈도우와 과거 윈도우의 z-정규화 피어슨 상관 "
                    "(경로 평균화 없음 — 개별 사례만 표시)"),
    "vol-v1": ("변동성 맥락 (vol-v1)",
               "실현변동성 = 일수익률 표준편차(윈도우) × √252 → 자기 역사 대비 백분위"),
    "regimes-v1": ("국면 대조 (regimes-v1)",
                   "에피소드 경계는 dd-v1로 도출(고점→저점→회복), 명명/메타데이터는 출처 있는 참조 데이터"),
}
