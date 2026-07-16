"""COST-IA — 어드민 좌측 네비 5-섹션 구조: 라우트→활성 서브탭·부모 섹션 매핑.

12개 평면 탭을 Overview + 4섹션(OPERATIONS/DATA/MONEY/ACCOUNTS)으로 묶었다. 라우트 핸들러는
그대로 page('/route', ...)만 호출하고, page()가 그 한 문자열로 활성 탭과 부모 섹션을 도출한다.
"""

from __future__ import annotations

from adminpanel.views import NAV_GROUPS, _nav_active, page


def test_nav_active_exact_and_subroute():
    assert _nav_active("/runs", "/runs") is True
    assert _nav_active("/runs", "/runs/123") is True        # 서브라우트도 활성
    assert _nav_active("/runs", "/runsomething") is False   # 접두 오탐 방지
    assert _nav_active("/db", "/db/controlplane/llm_usage") is True
    assert _nav_active("/data", "/db") is False
    assert _nav_active("/", "/") is True
    assert _nav_active("/", "/costs") is False               # 홈은 정확 일치만


def test_all_twelve_routes_present_exactly_once():
    hrefs = [h for _, items in NAV_GROUPS for (h, _l, _i) in items]
    assert len(hrefs) == 12 and len(set(hrefs)) == 12
    for r in ("/", "/pipelines", "/runs", "/queue", "/catalog", "/data",
              "/upstream", "/db", "/costs", "/billing", "/users", "/shares"):
        assert r in hrefs


def test_page_lights_active_tab_and_section_for_subroute():
    html = page("/runs/5", "Run 5", "<div>body</div>")
    assert "class='nav on' href='/runs'" in html      # 서브라우트가 Runs 탭을 켠다
    assert ">OPERATIONS<" in html                     # 부모 섹션 헤더 렌더
    assert "class='nav on' href='/costs'" not in html  # 다른 탭은 활성 아님


def test_page_renders_all_section_headers():
    html = page("/", "Overview", "x")
    for sec in ("OPERATIONS", "DATA", "MONEY", "ACCOUNTS"):
        assert f">{sec}<" in html
    # every route still renders as a link exactly once
    for _, items in NAV_GROUPS:
        for href, _l, _i in items:
            assert f"href='{href}'" in html
