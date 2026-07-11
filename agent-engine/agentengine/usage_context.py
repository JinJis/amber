"""METER-1 — per-request cost-attribution context.

studio-api가 요청마다 ``X-Project-Id``(그 유저의 control-plane 프로젝트)를 보내면, 엔드포인트가
이 contextvar에 심고 ``usage.report()``가 모든 Gemini 콜 텔레메트리에 실어 보낸다 — 유저별
LLM 원가가 측정 가능해진다(가격·캡 조정의 근거). contextvar라 동시 스트림 간에 절대 섞이지
않는다; 백그라운드 피드처럼 특정 유저가 없는 작업은 None(공용 원가)으로 남는다.
"""

from __future__ import annotations

from contextvars import ContextVar

_project_id: ContextVar[str | None] = ContextVar("vg_project_id", default=None)


def set_project(project_id: str | None) -> None:
    _project_id.set(project_id or None)


def current_project() -> str | None:
    return _project_id.get()
