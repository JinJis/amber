> **❌ §A 리서치 노트북은 2026-07-10 제거됨** — 이 문서는 히스토리 참고용. §B 스탠딩 알림은 유지(M-SA).

# 리서치 노트북 + 스탠딩 알림 — 구현 스펙 (2026-07-05)

> 사용자 방향: ① 대시보드를 갈아엎어 "근거를 pin해 기록하는 리서치 노트북 + 나만의 투자 노트 →
> 커뮤니티/SNS 공유"로, ② 알림봇 기능은 대시보드에서 제거, ③ 채팅 중 주기성 데이터에 대해
> 알림 구독을 자연스럽게 추천. 세부 설계는 현 서비스 흐름에 맞춰 아래와 같이 판단했다.
> 관련: [`UX_PROPOSALS.md`](./UX_PROPOSALS.md)(근거 패널 원장·엔트리), [`PUBLISH_SPEC.md`](./PUBLISH_SPEC.md)(공유 파이프라인).

---

# A. M-NB — 리서치 노트북 (대시보드 개편)

## A.1 판단 — 그리드 캔버스가 아니라 "세로 문서"다

기존 대시보드(FEATURE_DASHBOARD, 기본 OFF)는 react-grid-layout 캔버스 + 위젯별 알림 벨.
**리서치 기록의 자연 단위는 위젯 배치가 아니라 시간순 축적**이다 — 애널리스트의 노트는
"근거를 순서대로 쌓고 사이사이 내 생각을 적는" 문서다. 따라서:

- **모델**: 노트북 = 세로 블록 문서. 블록 = `pin`(근거 패널에서 담은 아티팩트/출처/원장 행/인용/verdict)
  또는 `text`(내 메모, markdown). 그리드 좌표(x/y/w/h) 폐기 → `order` 정수 하나.
- **핵심 습관 하나만 설계**: "담고, 왜 담았는지 한 줄 적는다." 모든 pin 블록에 선택적
  `note`(왜 담았는지) 필드 — 이것이 리서치 노트북과 스크랩북의 차이.
- **알림 완전 제거**: 노트북 화면에는 알림 UI가 없다. (알림봇 코드는 플래그 뒤에 동결 유지 —
  FLAG-1 결정과 일치. 알림의 후신은 §B 스탠딩 알림.)
- **M-NOTE 흡수**: PUBLISH_SPEC의 인사이트 노트(NT-1..4)는 별도 밀스톤이 아니라 노트북의
  공유 기능이 된다 — 노트북이 곧 노트의 초안이다.

## A.2 플로우

```
[채팅] 근거 패널                          [노트] (rail: 대시보드 → 노트로 개명)
  원장 행 / SourceCard / Artifact           ┌ 노트북 목록 (사이드) ┐  ┌ 노트북 문서 ────────────┐
   └ 📌 노트에 담기 ──────────────────────▶ │ · 반도체 사이클 리서치 │  │ 2026-07-05              │
      (피커: 최근 노트북 or 새로 만들기,     │ · 삼성전자 실적 추적   │  │ [pin] AAPL 10-K 매출 셀  │
       담을 때 "왜?" 한 줄 인라인 입력)       └──────────────────────┘  │  └ 메모: 가이던스 상회     │
                                                                      │ [text] ## 내 가설 …      │
                                                                      │ [pin] PER 도출 카드      │
                                                                      │ [pin] 낙폭 vs GFC 차트    │
                                                                      │ ─────────────────────── │
                                                                      │ [↗ 공유] [A4 내보내기]    │
                                                                      └─────────────────────────┘
```

- **담기 진입점** (모두 기존 컴포넌트에 버튼 1개): 근거 패널의 원장 행·SourceCard·ArtifactCard,
  SourceViewer(보다가 담기), 인용 카드(SH-4). 기존 `+대시보드` 핀 버튼을 `📌 노트`로 교체.
- **문서 편집**: 블록 세로 나열, 드래그 재정렬(order 스왑), 블록 사이 `+ 메모` 텍스트 블록,
  pin 블록의 `note` 인라인 편집. 리치 에디터 금지 — markdown textarea + 미리보기(기존 md 렌더 재사용).
- **공유 = 기존 SH 파이프라인** (신규 인프라 0): 노트북 → `POST /shares` kind=`note`,
  payload = 블록 스냅샷. 공개 페이지(`/s/{token}`)가 블록을 읽기 전용 렌더 + 블록별 출처 푸터.
  카드 이미지(SH-2b)의 A4 프리셋이 "리포트급 이미지"를 만든다.
- **정직성 레이아웃**: 공개 페이지에서 `text` 블록은 "✍ 작성자 메모" 라벨로 시각 분리,
  pin 블록은 provenance 푸터 유지 — 기계 검증된 근거와 사용자 의견이 섞여 보이지 않게.
  (사용자 텍스트는 QT-2로 기계 감사 불가 → 감사 대신 **귀속을 명시**하는 것이 정직한 답.)

## A.3 데이터 모델 (studio-api)

```python
class Notebook(Base):        # boards의 후신 (새 테이블 — 레거시 boards는 플래그 뒤 동결)
    id, user_email, title, created_at, updated_at

class NoteBlock(Base):
    id, notebook_id(fk), order(int)
    kind: str                # pin_artifact | pin_citation | pin_ledger | text
    payload: Text            # 스냅샷 JSON (pin: artifact/citation/ledger row 그대로; text: {md})
    note: Text | None        # "왜 담았는지" — pin 블록의 한 줄 메모
    created_at
```
- pin payload는 **스냅샷**(공유와 동일 원칙 — 노트는 나중에 봐도 그때 그 값). pin_artifact는
  `tool+args`를 품고 있어 "지금 값으로 새로고침" 버튼이 가능(기존 PinnedArtifact 재사용 패턴).
- API: `POST/GET/PATCH/DELETE /notebooks`, `POST /notebooks/{id}/blocks`,
  `PATCH /blocks/{id}` (order/note/payload-text), `DELETE /blocks/{id}`,
  `POST /notebooks/{id}/share` (SH 파이프라인 위임). BFF는 기존 프록시 패턴.

## A.4 태스크

| ID | 내용 | 크기 | 수용 기준 |
|---|---|---|---|
| **NB-1** | 모델+API: Notebook/NoteBlock CRUD + order 재정렬 + 스냅샷 계약 | M | pytest: CRUD·재정렬·payload 불변·사용자 격리 |
| **NB-2** | 담기 진입점: 원장 행·SourceCard·ArtifactCard·SourceViewer에 📌 + 피커(최근/새 노트북, 인라인 "왜?") — 기존 PinPicker 개조 | M | vitest: 각 진입점에서 블록 생성 payload 검증 |
| **NB-3** | 노트 화면: rail `대시보드`→`노트`, 목록+문서 뷰, 텍스트 블록 md 편집, 드래그 재정렬, pin 새로고침 | L | vitest: 블록 렌더·재정렬·메모 편집; 알림 UI 부재 |
| **NB-4** | 공유: kind=note 공개 페이지 블록 렌더(작성자 메모 라벨 분리) + A4 카드 이미지 | M | 공개 페이지 렌더·메모/근거 시각 분리·SH-2b A4 |
| **NB-5** | eval/e2e: 노트북 CRUD e2e + 공유 스냅샷 불변 | S | green |

레거시 처리: BoardCanvas·AlertSheet·PinPicker(보드용)·FEATURE_DASHBOARD 경로는 **삭제하지 않고
동결**(FLAG-1과 동일 원칙) — rail 노출만 노트로 교체. M6에서 재평가.

---

# B. M-SA — 스탠딩 알림 (채팅 속 자연 추천)

## B.1 판단 — "알림 설정"이 아니라 "질문 구독"이다

알림봇(채널·임계값 UI)은 챗-퍼스트와 어긋나 꺼둔 상태. 사용자가 원하는 것은 "채팅하다가
주기적으로 받아볼 만한 것을 자연스럽게 추천"이다. 우리 데이터 계약에는 이미
**cadence**(intraday/daily/event/scheduled/streaming/one_shot)가 모든 인용·아티팩트에 실려 있다
— 이것이 추천의 트리거다. 설계 원칙:

1. **단위는 '질문'이다.** 임계값·지표 설정이 아니라 방금 한 질문 그대로 구독: "이 질문 계속
   지켜보기". 설정 화면 없이 원탭.
2. **전달은 데스크다.** 푸시 채널(텔레그램 등)을 되살리지 않는다. 변화가 생기면 **다음 방문의
   데스크 피드 카드**(kind `standing_update`)로 도착 — pull→push 철학의 챗-퍼스트 구현.
   (외부 채널은 M6에서 재평가.)
3. **추천은 조용히, 한 번만.** 답변이 끝나고 그 답의 근거 중 `cadence != one_shot`이 있을 때만,
   팔로업 칩 줄에 **알림 칩 1개**를 맨 끝에 추가: `🔔 이 질문 계속 지켜보기`. 같은 대화에서
   재노출하지 않는다(억지 권유 금지).

## B.2 플로우

```
[답변 완료] 팔로업 칩: [매출 추이는?] [경쟁사는?] … [🔔 이 질문 계속 지켜보기]
   └ 탭 → 인라인 확인 한 줄: "실적 발표·공시가 뜨면 데스크에 알려드릴게요 — 구독 [확인] [취소]"
      └ 확인 → StandingQuestion 생성 (질문·티커·소스 cadence에서 파생한 체크 주기)

[다음 방문] 데스크 피드 상단:
   ┌ 🔔 지켜보던 것 ─────────────────────────────┐
   │ "삼성전자 실적 어때?" — 새 잠정실적 공시 접수    │
   │  "확인하기" → 그 질문으로 새 턴 (컴포저 채움)    │
   └────────────────────────────────────────────┘

[관리] 관심 탭 하단 "지켜보는 질문 N" — 목록·마지막 확인 시각·해제. 유저당 캡(10).
```

## B.3 구현 — 데스크 피드에 피기백 (신규 워커 0)

- **모델(studio-api)**: `StandingQuestion{id, user_email, question, ticker?, market?,
  cadence(daily|event|weekly ← 소스 cadence에서 파생), last_checked_at, last_signature, active}`.
- **체크 로직**: 데스크 피드 생성(DK-1, 이미 관심그룹을 게이트웨이로 훑음)이 실행될 때 활성
  스탠딩 질문도 함께 평가 — 질문의 핵심 소스(생성 시 기록한 tool+args)를 재호출해
  **signature**(최신 as_of/최근 공시 accession/최근 bar 날짜)를 비교, 달라졌으면
  `standing_update` 카드 생성. LLM 재실행이 아니라 **서명 비교**라 저렴·결정론적.
- **agent-engine**: 답변 done 이벤트에 `standing_offer` 필드(그 턴 인용의 최소 cadence +
  구독 시 기록할 tool+args 시그니처 소스) — 프론트 칩 노출 게이트. 이미 인용에 cadence가 있어
  집계만 하면 됨.
- **가드레일 정합**: 카드 문구는 항상 사실 서술("새 공시 접수", "실적 발표됨") — 예측·매수 신호
  아님. 기존 데스크 카드 감사·인용 규칙 그대로 적용.

## B.4 태스크

| ID | 내용 | 크기 | 수용 기준 |
|---|---|---|---|
| **SA-1** | done 이벤트 `standing_offer`(cadence 집계 + 시그니처 소스) + 프론트 알림 칩(1회 노출·인라인 확인) | M | pytest: periodic 근거 있을 때만 offer; vitest: 칩→확인→POST |
| **SA-2** | StandingQuestion 모델+API (생성·목록·해제·캡 10) | S | pytest CRUD·캡 |
| **SA-3** | 데스크 피기백 체크: 서명 비교 → `standing_update` 카드 (문구 사실 서술, 질문 딥링크) | M | pytest: 서명 변화→카드 생성·불변→미생성; 카드 인용 게이트 통과 |
| **SA-4** | 관리 UI(관심 탭 목록·해제) + eval 시나리오(제안 노출·미주기 소스엔 미노출) | S | green |

---

# C. 로드맵 배치 (구현 순서)

```
[진행 중]  LG-1..5 (수치 원장 + 클릭 가능한 [n]) → ENT-1..5 (관제탑 엔트리)
[다음]     NB-1 → NB-2 → NB-3 → NB-4 → SA-1 → SA-2 → SA-3 → NB-5·SA-4
```
- LG의 [n] 클릭(사용자 직접 요청)은 LG-3에 포함: **[n] = 근거 패널의 해당 카드로 스크롤+플래시,
  더블 목적지(카드에서 원문 열기)** — 본문 각주를 죽은 태그에서 근거 패널의 리모컨으로.
- NB가 SA보다 먼저(담기 흐름이 원장/패널 UI에 붙어 있어 LG 직후가 저비용).
- M-NOTE(NT-1..4)는 M-NB로 흡수 — ROADMAP에서 상태 변경.
```
