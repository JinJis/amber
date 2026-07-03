# Value Graph 서비스 분석

_Started 2026-06-15 16:01 UTC_

---

## User

GitHub connected

## Assistant

GitHub's connected! Paste any github.com repository URL (repo, folder, or file) and I'll explore its structure, then pull in the specific files I need as reference for building your design mockups.

## User

https://github.com/JinJis/value-graph/tree/main 이거 서비스 분석해서 와이어프레임 작성해. 

Admin 관련한거는 무시하고 특히 https://github.com/JinJis/value-graph/blob/main/docs/ROADMAP.md, https://github.com/JinJis/value-graph/blob/main/docs/UX_SPEC.md, https://github.com/JinJis/value-graph/blob/main/docs/ARCHITECTURE.md

이 문서들을 참고해.

## Assistant

GitHub 저장소를 살펴보고 문서들을 읽어보겠습니다.

_[tool: github_read_file]_

_[tool: github_read_file]_

_[tool: github_read_file]_

문서를 충분히 분석했습니다. 이건 **개인 투자 리서치 데스크** 서비스네요 — 챗봇이 아니라, 사용자가 자기 관심종목(watchlist)에 "상주 애널리스트"를 배치하고, 모든 수치가 출처·시점·신선도가 붙은 live artifact로 나오며, 묻기 전에 변화를 먼저 push해주는 제품. 핵심 3기둥은 **신뢰(출처 보장) · Pull→Push · 생태계(갤러리 클론)**.

와이어프레임을 시작하기 전에, 탐색 방향을 좁히기 위해 몇 가지만 확인할게요.

_[tool: questions_v2]_

## User

Questions answered:
- screens: 전부 다 (앱 전체 맵)
- breadth: 2~3개 — 핵심 화면만 변주
- fidelity: 중간 — 깔끔한 그레이스케일 박스, 약간의 신뢰색 액센트
- device: 데스크탑 (3-pane 데스크 셸 중심)
- focus: 정보 구조 / 내비게이션 (앱 셸), 신뢰 시각화 (출처·신선도·가드레일 라벨)
- mascot: 네 — 자리(placeholder)로 표시

## Assistant

이 제품을 잘 파악했습니다. 풀 앱 맵 + 핵심 화면(데스크) 변주, 중간 충실도 그레이스케일 + 신뢰색 액센트, 데스크탑 3-pane 셸 중심으로 와이어프레임을 만들겠습니다. 계획을 세우고 바로 시작할게요.

_[tool: update_todos]_

_[tool: dc_write]_

Section A is in. Let me verify it renders, then append the remaining sections.

_[tool: show_html]_

_[tool: show_html]_

깔끔하게 렌더됩니다. 나머지 섹션들을 이어 붙이겠습니다.

_[tool: dc_html_str_replace]_

_[tool: dc_html_str_replace]_

_[tool: dc_html_str_replace]_

All sections are in. Let me verify the full render.

_[tool: update_todos]_

_[tool: multi_screenshot]_

_[tool: multi_screenshot]_

The later scroll positions aren't advancing — let me check whether all sections actually rendered.

_[tool: eval_js]_

Only Section A rendered — the appends may have hit a parse issue. Let me reload and check logs.

_[tool: show_html]_

_[tool: eval_js]_

_[tool: get_webview_logs]_

All 9 sections render cleanly now (the earlier read was stale). Let me verify the later sections look right.

_[tool: multi_screenshot]_

전부 깔끔하게 렌더됩니다. 완성되었으니 마무리하겠습니다.

_[tool: update_todos]_

_[tool: done]_

`Value Graph 와이어프레임.dc.html` 완성했습니다.

**문서 분석 → 와이어프레임 (9개 섹션, 앱 전체 맵):**
- **A · 정보구조 & 앱 셸** — 3-pane 데스크 해부(레일·중앙·LIVE 컨텍스트), 6개 목적지 맵, Pull→Push 루프
- **B · 데스크 변주 3종** (핵심 화면) — 3-pane 클래식 / artifact-forward / 몰입형+thinking
- **C · 신뢰 시각화** — 소스 프리뷰 카드 3종(공시 verbatim·지표 계산·뉴스 맥락), 신선도 레전드, 가드레일 라벨
- **D · 보드** · **E · 분석가+빌더(NL↔폼, 툴 투명성)** · **F · 관심종목/@그룹** · **G · 브리프 인박스** · **H · 갤러리+클론 위저드** · **I · 온보딩 플로우**

**시스템:** 그레이스케일 박스 + 1px 헤어라인, Space Grotesk(UI)/Space Mono(숫자·티커·타임스탬프), 신선도 신호(🟢🟡🔴⚪)만 색을 갖는 원칙, 픽셀 마스코트는 체커 placeholder, 손글씨(Caveat) 주석으로 의도 설명.

**참고할 점:**
- 데이터·카피는 UX_SPEC 예시 기반의 더미입니다(₩가격, 날짜 등). 실제 값은 자리표시예요.
- 중간 충실도라 인터랙션(hover 팝오버, 위저드 단계 전환)은 "상태를 한 컷씩" 보여주는 정적 표현입니다.
- 다음 단계로 특정 화면(예: 데스크나 빌더)을 골라 고충실도 목업으로 올리거나, 변주를 더 추가할 수 있습니다.

