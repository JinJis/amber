"""COST-3 — 백그라운드 스윕 상류 호출 계측 (opt-in).

http._note_provider_call이 제공자별로 세고 임계치에서 배치 flush한다. 플래그 off면 완전 무동작
(핫 패스에 영향 0). fetch가 넘기는 provider 이름을 그대로 쓰므로 호스트 파싱이 필요 없다.
"""

from __future__ import annotations

import app.http as H
from app.config import settings


def _reset():
    H._provider_calls.clear()
    H._pu_last_flush = 0.0


def test_flag_off_is_noop(monkeypatch):
    _reset()
    monkeypatch.setattr(settings, "provider_usage_telemetry", False)
    for _ in range(10):
        H._note_provider_call("yahoo")
    assert H._provider_calls == {}


def test_counts_per_provider_no_flush_under_threshold(monkeypatch):
    _reset()
    monkeypatch.setattr(settings, "provider_usage_telemetry", True)
    monkeypatch.setattr(settings, "provider_usage_flush_seconds", 9999)   # 시간 flush 방지
    captured: list = []
    monkeypatch.setattr("app.telemetry.report_provider_usage", lambda c: captured.append(c))
    for _ in range(3):
        H._note_provider_call("yahoo")
    for _ in range(2):
        H._note_provider_call("sec_edgar")
    assert H._provider_calls == {"yahoo": 3, "sec_edgar": 2}
    assert captured == []          # 임계치 미만 → flush 없음


def test_flush_at_threshold_batches_and_resets(monkeypatch):
    _reset()
    monkeypatch.setattr(settings, "provider_usage_telemetry", True)
    monkeypatch.setattr(settings, "provider_usage_flush_seconds", 9999)
    monkeypatch.setattr(H, "_PU_FLUSH_CALLS", 5)
    captured: list = []
    monkeypatch.setattr("app.telemetry.report_provider_usage", lambda c: captured.append(c))
    for _ in range(5):
        H._note_provider_call("yahoo")
    assert captured == [{"yahoo": 5}]   # 임계치 도달 → 한 번에 배치 flush
    assert H._provider_calls == {}       # 리셋됨 (다음 창부터 새로 누적)
