#!/usr/bin/env bash
# RQ-8 — RAG recall@k 측정 (실행 중인 스택의 실코퍼스 대상; 스택 무접촉 read-only).
# 개선 전/후로 돌려 수치를 커밋 메시지에 기록한다.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 eval/rag_recall.py --url "${RAG_URL:-http://localhost:8002}" --k "${K:-8}"
