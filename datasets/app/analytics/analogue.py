"""Analogue search (HL-3) — the k most similar historical price paths to a recent window.
HISTORY_LAB_SPEC §4.5.

Similarity = Pearson correlation of z-normalized cumulative log-return paths. Matches are
non-overlapping (non-max suppression). We NEVER average the matches into one path — that would
manufacture a forecast-looking line (ROADMAP §2). Each match carries its own ``aftermath`` (what
actually happened next), drawn as history, right of day 0, under the historical label.
"""

from __future__ import annotations

import math

from app.analytics._common import LABEL, Bar, clean_bars, pearson, rebase, zscore

METHOD = "analogue-v1"


def _cum_logret_path(closes: list[float]) -> list[float]:
    """Cumulative log-return path anchored at 0 (shape of the move, level-independent)."""
    out = [0.0]
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(out[-1] + math.log(closes[i] / closes[i - 1]))
        else:
            out.append(out[-1])
    return out


def analogues(query_bars: list[Bar], candidates: dict[str, list[Bar]],
              window: int = 120, k: int = 5, step: int = 5) -> dict:
    """Find the top-k historical windows most similar to the last ``window`` bars of ``query_bars``.

    ``candidates``: {label: bars} to search (e.g. the same ticker's history + index anchors). Each
    match: {ticker(label), start_date, end_date, score, path (rebased 100), aftermath (rebased 100)}.
    Non-overlapping via greedy selection with a min-gap of window//2 between chosen starts.
    """
    q = clean_bars(query_bars)
    if len(q) < window:
        return {"method": METHOD, "label": LABEL, "window": window, "matches": [],
                "error": "insufficient_query_history", "required": window, "available": len(q)}
    q_recent = q[-window:]
    q_z = zscore(_cum_logret_path([c for _, c in q_recent]))
    if q_z is None:
        return {"method": METHOD, "label": LABEL, "window": window, "matches": [],
                "error": "degenerate_query_window"}

    q_start = q_recent[0][0]
    q_end = q_recent[-1][0]
    scored: list[dict] = []
    for label, cand in candidates.items():
        cb = clean_bars(cand)
        for s in range(0, len(cb) - window + 1, max(1, step)):
            win = cb[s:s + window]
            # skip a window that overlaps the query window in time (a self-match)
            if win[-1][0] >= q_start and win[0][0] <= q_end:
                continue
            cz = zscore(_cum_logret_path([c for _, c in win]))
            if cz is None:
                continue
            score = pearson(q_z, cz)
            if score is None:
                continue
            after = cb[s + window:s + 2 * window]  # what happened next (drawn as history)
            scored.append({
                "ticker": label, "start_date": win[0][0].isoformat(), "end_date": win[-1][0].isoformat(),
                "start_idx": s, "score": round(score, 4),
                "path": rebase([c for _, c in win]),
                "aftermath": rebase([c for _, c in ([win[-1]] + after)]) if after else [],
                "_source": label,
            })

    # greedy non-overlap by descending score (min-gap window//2 within the same series)
    scored.sort(key=lambda m: m["score"], reverse=True)
    chosen: list[dict] = []
    taken: dict[str, list[int]] = {}
    gap = max(1, window // 2)
    for m in scored:
        starts = taken.setdefault(m["_source"], [])
        if all(abs(m["start_idx"] - t) >= gap for t in starts):
            chosen.append({kk: vv for kk, vv in m.items() if kk not in ("start_idx", "_source")})
            starts.append(m["start_idx"])
        if len(chosen) >= k:
            break

    return {
        "method": METHOD, "label": LABEL, "window": window, "anchor": "now",
        "current": {"label": "현재", "path": rebase([c for _, c in q_recent])},
        "matches": chosen,
    }
