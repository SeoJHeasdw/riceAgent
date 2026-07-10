# RICE 시각 규율 (Visual Discipline)

IBM Carbon 토큰 위에 얹는 RICE의 시각 규칙. 목표는 "더 꾸미기"가 아니라
**덜 말하고 더 정확하게 보이기** — Cursor / Raycast / Linear급 데스크톱 밀도.

디자인 리뷰(2026-07)에서 확정된 규칙이며, 코드 리뷰 시 이 문서를 기준으로 판정한다.

## 1. Surface 역할 고정

| 역할 | 토큰 | 비고 |
| --- | --- | --- |
| **brand shell** | `bg-shell` + `border-shell-border` | titlebar + IconRail 전용. 남색 기운의 near-black(`--rice-shell`) — RICE 프레임 |
| 앱 배경 | `bg-background` | 화면 바탕 |
| 패널·카드 | `bg-layer-01` + `border-border-subtle-01` | 커스텀 그라디언트 금지 |
| elevated 표면 | `bg-layer-02` | 카드 안의 타일, 팝오버 |
| 모달 backdrop | `bg-black/50 backdrop-blur-sm` | `surfaces.ts` 단일 출처 |

- 팝업 표면은 반드시 [surfaces.ts](../src/components/ui/surfaces.ts)의 `dialogBackdropCls` / `dialogSurfaceCls`를 쓴다.
- **shell은 브랜드가 사는 유일한 상시 표면**이다. titlebar·IconRail 밖으로 확장 금지
  (콘텐츠까지 어두워지면 프레임 효과가 사라진다). shell 위 상태는 white/alpha로:
  active `bg-white/[0.09] text-white`, hover `bg-white/[0.06]`, 구분선 `bg-white/10`.

## 2. Elevation — 평면이 기본

- **평면 카드에는 그림자 없음.** border가 형태를 잡는다 (`Card` 기본값).
- 그림자는 떠 있는 표면에만: 팝오버·툴팁 = `shadow-elevation-2`, 모달·드로어·토스트 = `shadow-elevation-3`.
- `shadow-overlay`, `shadow-glow-*` 토큰은 제거됨 — 다시 추가하지 말 것.
- hover는 **색/border 변화만**: `hover:border-border-strong-01`, `hover:bg-layer-hover-01`.

## 3. Radius

- 일반 모달·다이얼로그·카드: `rounded-xl` (12px). `rounded-2xl`는 채팅 말풍선 등 예외 표면만.
- confirm 다이얼로그: `max-w-[420px]`.

## 4. 타입·폼 스케일 (데스크톱 밀도)

- 입력 높이 `h-9` (조밀한 곳은 `h-8`), padding `px-3`.
- label `text-label-01`, 본문 `text-body-short-01`, 모달 제목 `text-heading-01`.
- 모달 body 간격 `space-y-4`. 버튼 표준 `size="sm"`(h-8) — 모달 CTA에 `h-12` 금지.
- **임의 폰트 크기(`text-[..px]`) 금지.** Carbon 스케일 아래 유일한 공식 마이크로 단계는
  `text-label-micro`(11px) — 배지·Kbd·그룹라벨 전용. 9~11.5px 임의값은 전부 이걸로,
  12~13px는 `caption-01`/`body-short-01`로 스냅한다.

## 4b. 색 토큰 규율

- **임의 hex 유틸(`bg-[#...]` 등) 금지.** [tailwind.config.js](../tailwind.config.js)에
  이름 붙은 Carbon 팔레트 단계(blue/red/green/yellow/orange/purple/teal/magenta)만 쓴다.
  필요한 단계가 없으면 config에 추가하는 것이 정답이지, 임의값이 아니다.
- 버튼 hover/active는 시맨틱 토큰: `bg-interactive-hover/active`, `bg-danger-hover/active`
  (globals.css `--cds-interactive-hover` 등과 동기 — 테마 전환 대비).
- diff 표면(추가/삭제 행)은 `bg-diff-add-bg text-diff-add-text` 계열 시맨틱 토큰.
- 허용된 리터럴 hex: macOS 트래픽 라이트 목업, 외부 프로바이더 브랜드 색(inline style).

## 4c. 키보드 포커스

- 전역 `:focus-visible { outline: 2px solid var(--cds-focus); outline-offset: 2px }`가
  기본값 — 개별 컴포넌트에 focus 클래스를 살포하지 않는다.
- `outline-none`은 **부모가 `focus-within`으로 포커스를 표시하는 내장 input**에만 허용.
- 모달·확인창·드로어는 열릴 때 내부 첫 컨트롤로 포커스를 옮기고, Tab을 가두며,
  닫힐 때 원래 트리거로 포커스를 돌려준다.
- 리사이즈 구분선은 포인터뿐 아니라 `←`/`→`(8px), `Shift` 조합(32px), `Home`(기본값 복원)으로 조절한다.

## 4e. 테마·글꼴

- 모든 콘텐츠 표면은 시맨틱 Carbon 토큰만 사용해 `[data-theme='dark|light']`를 함께 지원한다.
- brand shell은 테마와 관계없이 어둡게 유지해 앱의 고정 프레임 역할을 한다.
- UI 글꼴은 `--font-sans`를 단일 진입점으로 사용한다. 시스템·IBM Plex Sans·Mono 선택이
  화면별 하드코딩 없이 전체 앱에 적용돼야 한다.

## 4d. 화면 헤더 리듬

화면 전환 시 제목 기준선이 튀지 않도록 헤더 패딩은 아래 4가지 외 변주 금지.

| 컨텍스트 | 패딩 | 제목 |
| --- | --- | --- |
| 풀페이지 (Home·Settings·마켓 헤더) | `px-8` (마켓 헤더 `pt-6`) | `text-heading-03 font-light` |
| 마스터-디테일 레일 타이틀 | `px-5 pt-5 pb-3` | `text-heading-03 font-light` |
| 디테일 헤더 (아이콘+제목+태그) | `px-7 pt-6 pb-5 border-b` | `text-heading-03 font-semibold` |
| 디테일 body | `px-7 pt-5 pb-10` (+`gap-7`) | — |

## 5. Blue 사용 규칙

blue(`interactive`)는 **primary action과 current selection에만**.

- 선택 상태: `bg-layer-selected-01` (+ 선택 카드라면 `border-interactive` 1px). `bg-interactive/15` 워시 금지.
- 정보 카드·hover·아이콘 타일은 neutral: 타일은 `bg-layer-02 border-border-subtle-02`에 아이콘 틴트만.
- 상태(활성/연결됨)는 파란 채움이 아니라 아이콘 톤(`text-icon-primary` vs `text-icon-secondary`)과 Tag로.

## 6. Motion 규칙

| 대상 | 허용 모션 |
| --- | --- |
| 네비게이션 | fade only |
| 카드 | color/border 변화만 (`transition-colors duration-150`) |
| 모달 | scale/fade (`animate-scale-in`) |
| 라이브 상태 | pulse only (예: 학습 루프 "학습 중" 도트) |

`hover:-translate-y-*`, `active:scale-*`, `hover:brightness-*`, hover aura/blur, `animate-ping`(라이브 상태 외) 금지.

**Duration 표준: `duration-150`.** 예외는 셋뿐 — 모달 scale-in 180ms, 패널 슬라이드 200ms,
세그먼트 인디케이터 200ms. 300ms+는 온보딩 무대 연출에만 허용. 100ms 이하 금지(뚝 끊겨 보임).

## 7. AI-native 시각 문법

"학습됨 / 기억됨 / 제안됨 / 스킬화됨" 순간에만 쓰는 전용 문법 — 일반 카드에 사용 금지.

- `.ai-label` (그라디언트 AI pill), `.ai-tile` (좌측 그라디언트 액센트 타일)
- 섹션 단위: `border-l-2 border-interactive/70` + `bg-interactive/[0.04]`
- 적용처: ChatMessage의 학습 카드·스킬 개선 제안, LearningPanel 제안함, HomeScreen 학습 현황, 빈 채팅의 "RICE가 기억하는 것".
- **ai-tile의 그라디언트 보더는 오버라이드 금지.** 색을 바꾸고 싶다면 그건 AI 문법이
  아니라는 신호다 — 도구 실행 승인처럼 경고 성격이면 `border-l-2 border-l-support-warning`
  의 warning 카드로 분리한다.

## 8. Danger 톤

- 파괴적 확인은 차갑게: 빨간 면적은 **confirm 버튼과 작은 아이콘 틴트(`bg-support-error/10`)만**.
- 상단 색 바·대형 경고 아이콘 금지. 아이콘 `w-6 h-6`, 본문 `leading-5`, confirm `h-8 min-w-[72px]`.

## 9. 예외 허용 목록

아래는 **위반이 아니라 의도된 예외**다. 이 목록에 없는 `bg-gradient-*`,
`rounded-2xl`, `bg-interactive/15`, CTA `h-12`는 전부 위반으로 본다.

| 예외 | 허용 위치 | 근거 |
| --- | --- | --- |
| titlebar `R` 로고의 brand gradient | `Layout` shell logo | 앱 프레임 안의 영구 브랜드 표식 |
| `gradient-skill` 등 도메인 그라디언트 | 채팅 스타터 아이콘, 슬래시 커맨드 chip 타일, 전송 버튼 | 스킬/AI 순간의 identity 표식 |
| `gradient-brand` + `rounded-2xl` 로고 타일 | Settings About 카드 | brand 표면 |
| `rounded-2xl rounded-br-sm` | 채팅/드로어/온보딩 사용자 말풍선 | 말풍선 형태 문법 |
| `rounded-2xl`, `bg-interactive/15` | onboarding/ 하위 (일러스트 타일·스텝 인디케이터) | 1회성 온보딩 무대 |
| `bg-{support-*,interactive}/15` variant glow | Notification/Progress 토스트 아이콘 배지 | 상태색 시스템 (4색 semantic) |
| 섹션별 다색 아이콘 칩 (`/15` 틴트) | SettingsScreen 섹션 헤더 | 긴 설정 페이지의 스캔 보조 — blue 단독 아님 |
| `animate-ping` | LearningPanel "학습 중" 도트 | 라이브 상태 pulse |
| 스탯/파일 수 숫자의 다색 틴트 | PluginCard composition 스트립 | 정보 분류 색, 면적 없음 |
