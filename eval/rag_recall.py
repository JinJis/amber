"""RQ-8 — RAG recall 하니스: 실행 중인 rag(:8002)의 실코퍼스에 등급 쿼리셋을 던져
recall@k·MRR을 측정한다. 개선 전/후 수치를 커밋 메시지에 기록하는 용도.

판정은 본문 문자열이 아니라 **provenance 술어**(ticker/doc_type) — 재인제스트로 청크가
바뀌어도 강건하다. 코퍼스에 해당 소스가 아직 없으면 그 항목은 'n/a'로 표시(0점 아님).

    python3 eval/rag_recall.py [--url http://localhost:8002] [--k 8]
"""

from __future__ import annotations

import argparse
import json
import urllib.request

# (질문, 술어: provenance dict → bool, 라벨)
GRADED = [
    ("Apple supply chain concentration risk",
     lambda p: p.get("ticker") == "AAPL" and (p.get("doc_type") or "").lower() not in ("news",), "US 공시(AAPL 리스크)"),
    ("애플 공급망 리스크", lambda p: p.get("ticker") == "AAPL", "한→영 교차(AAPL)"),
    ("NVIDIA data center revenue growth", lambda p: p.get("ticker") == "NVDA", "US 재무 서사(NVDA)"),
    ("삼성전자 영업이익 발표", lambda p: p.get("ticker") in ("005930", "005930.KS"), "KR 실적(삼성)"),
    ("SK하이닉스 HBM", lambda p: p.get("ticker") in ("000660", "000660.KS"), "KR 종목(하이닉스)"),
    ("Microsoft cloud Azure growth", lambda p: p.get("ticker") == "MSFT", "US 종목(MSFT)"),
    ("management guidance on margins earnings call",
     lambda p: (p.get("doc_type") or "") == "transcript", "어닝콜(트랜스크립트)"),
    ("최근 반도체 업황 뉴스", lambda p: (p.get("doc_type") or "") == "news", "KR 뉴스"),
]


def search(url: str, query: str, k: int) -> list[dict]:
    req = urllib.request.Request(f"{url}/rag/search", method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps({"query": query, "top_k": k}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("hits", [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8002")
    ap.add_argument("--k", type=int, default=8)
    a = ap.parse_args()
    hit = total = 0
    mrr = 0.0
    for q, pred, label in GRADED:
        try:
            hits = search(a.url, q, a.k)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {label:24s} — 요청 실패: {exc}")
            continue
        rank = next((i + 1 for i, h in enumerate(hits) if pred(h.get("provenance") or {})), None)
        if not hits:
            print(f"  · {label:24s} — n/a (코퍼스 없음)")
            continue
        total += 1
        if rank:
            hit += 1
            mrr += 1.0 / rank
        print(f"  {'✓' if rank else '✗'} {label:24s} rank={rank or '-'}  q=\"{q[:36]}\"")
    if total:
        print(f"\nrecall@{a.k}: {hit}/{total} = {hit/total:.2f} · MRR: {mrr/total:.3f}")
    else:
        print("\n측정 불가 — rag가 떠 있고 코퍼스가 인제스트돼 있어야 해요.")


if __name__ == "__main__":
    main()
