"""ANCHOR-NORM — 묶음 인용([1,2]·[3-5])이 절대 생텍스트로 남지 않도록: 정규화 + 스트림 경계."""

from __future__ import annotations

from agentengine.anchors import AnchorStream, normalize_anchor_groups


def test_groups_expand_to_individual_markers():
    assert normalize_anchor_groups("근거는 [1,2]와 [3, 4] 그리고 [5·6]") == "근거는 [1][2]와 [3][4] 그리고 [5][6]"
    assert normalize_anchor_groups("범위 [2-5] 인용") == "범위 [2][3][4][5] 인용"
    assert normalize_anchor_groups("[3,4,5,6] 전부") == "[3][4][5][6] 전부"


def test_singles_links_and_years_untouched():
    assert normalize_anchor_groups("단일 [7]과 [링크](https://x.y) 유지") == "단일 [7]과 [링크](https://x.y) 유지"
    assert normalize_anchor_groups("연도 [2024, 2025]는 그대로") == "연도 [2024, 2025]는 그대로"


def test_stream_holds_partial_bracket_across_chunks():
    ns = AnchorStream()
    out = ns.feed("매출이 늘었어요 [1,")
    assert out == "매출이 늘었어요 " and "[1," not in out   # 꼬리 보류
    out2 = ns.feed("2] 그리고 [3")
    assert out2 == "[1][2] 그리고 "                          # 다음 청크에서 정규화 방출
    assert ns.feed("-5] 끝.") == "[3][4][5] 끝."
    assert ns.flush() == ""


def test_stream_flush_releases_tail():
    ns = AnchorStream()
    ns.feed("근거 [2,")
    assert ns.flush() == "[2,"[:0] + normalize_anchor_groups("[2,")   # 불완전 꼬리는 원문 그대로(날조 없음)
