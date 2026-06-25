# 작업: learning — 학습 루프 (memory · skill · curator · loop · user_model)

> 먼저 `CONVENTIONS.md`를 읽었다고 가정한다.
> **정본: `../RICE_LEARNING_LOOP_DESIGN.md` 전체** + `../RICE_RUNTIME_DESIGN.md` §3·§4.
> 이건 RICE의 **핵심 차별점**이자 **최대 규모** 모듈이다. 아래 서브태스크(T1~T5)
> 순서대로, 가능하면 **여러 세션으로 나눠** 진행하라. 각 T 끝에 테스트를 붙여라.

## 전체 그림

`learning/service.py`(파사드, `ILearningService` 구현)가 하위 모듈을 **조합**한다.
외부(orchestrator·routes)는 `service.py`만 본다. 너는 하위 파일을 만들고 파사드가
그것들을 호출하게 한다. 트리(RUNTIME_DIRECTORY §2):

```
learning/
  interfaces.py        # 동결 — 구현 대상 (읽기)
  service.py           # 파사드 — 스텁 본문을 실제 위임으로 교체
  loop.py              # T4 — 턴 관찰→효과 산출 (생성/메모리/patch 결정)
  memory/store.py      # T1 — facts/preferences CRUD (state.db)
  memory/search.py     # T1 — FTS5 검색 (memories + 세션)
  skill/store.py       # T2 — ~/.rice/skills/*.md + .usage.json + .history/
  skill/lifecycle.py   # T2/T3 — 생성·상태전이
  skill/patch.py       # T3 — 개선 제안 산출·적용·version++·.history
  skill/usage.py       # T2 — use/view/patch 카운터
  curator/curator.py   # T3 — 자동 시간규칙 전이 + 수동 "스킬 정리"
  user_model/profile.py# T5 — 개인화/답변 스타일
  tests/
```

`ILearningService`(읽어라) 메서드 = 파사드가 제공할 표면:
`prefetch`, `observe`, `apply_patch`, `list_skills`, `get_skill`, `curate`,
`set_state`, `history`, `rollback`, `list_memories`.

반환 DTO(`SkillSummary/SkillDetail/SkillVersion/CurateResult/PrefetchResult/
SkillCreated/MemoryWritten/PatchProposal/LearningEffect`)는 모두 `interfaces.py`에
이미 정의돼 있다. **그 DTO를 그대로 반환**하라(새 타입 만들지 말 것).

`state.db` 스키마는 이미 있다: `core/storage/migrations/0001_init.sql`의 `memories`,
`memories_fts`(트리거 포함), `messages`, `messages_fts`. **새 마이그레이션이
필요하면** `core/storage/migrations/0002_*.sql`로 추가한다(기존 0001 수정 금지).

`service.py`는 현재 `(db, config.learning)`만 받는다. **스킬은 파일시스템
(`~/.rice/skills/`)에 산다** → 너는 `RicePaths`도 필요하다. `composition_root.py`에서
`LearningService(db, config.learning, paths)`로 **DI를 추가**하라(허용된 수정).

---

## T1 — Memory (memory/store.py, memory/search.py) · 정본 LEARNING_LOOP §6, RUNTIME_DESIGN §3

메모리 종류(테이블 `memories.kind`): `fact` | `preference`.
- **작성 규칙(중요):** **선언적 사실만** facts에 넣는다. 절차(방법)는 메모리가
  아니라 **스킬**이다. 예: "Project uses pytest" ✓ / "Run tests with pytest -n 4" ✗.
- `store.py`: `add(kind, content, source_session_id)`, `list()`, (선택)삭제. INSERT는
  FTS 트리거가 자동 색인한다(0001 참조) — 추가 작업 불필요.
- `search.py`: **FTS5만.** `search_memories(query, k)` = `memories_fts MATCH ?`로 상위
  k. 세션 검색은 `messages_fts`(이미 `SessionManager.search`가 있으니, 메모리는
  메모리대로 별도 함수). **임베딩/시맨틱 금지.**
- **prefetch 입력으로 쓰임**(T4): 최근 facts N개 + 쿼리 FTS 매칭 상위 K개. N·K
  기본값은 `config.learning`(이미 `prefetch_recent_facts`, `prefetch_fts_top_k` 존재).

수용: facts/prefs 추가→`list_memories()`로 조회되고, FTS 검색이 키워드로 매칭됨.

---

## T2 — Skill 저장·포맷·usage (skill/store.py, usage.py, lifecycle.py 일부) · 정본 LEARNING_LOOP §2·§3

**SKILL.md 포맷(정본 §2)** — frontmatter + 본문. 필수 frontmatter:
`name`(kebab, 고유), `version`(정수), `description`(한 줄), `state`(active|stale|archived).
선택: `tags`, `source`, `created_at`, `updated_at`. **마켓 artifact의 skills/와 동일
포맷**이라 plugin 제공 스킬과 호환돼야 한다(CONTRACTS §2.2).

`store.py` 책임:
- 디렉토리: `~/.rice/skills/<name>.md`(또는 `<name>/SKILL.md` — 트리는 `*.md` +
  `<name>/.history/`를 쓰므로 **`~/.rice/skills/<name>.md` + `~/.rice/skills/<name>/.history/v<N>.md`**
  조합을 권장. 확실치 않으면 NOTE). `RicePaths.skills`, `RicePaths.skill_history(name)` 사용.
- frontmatter 파싱/직렬화(간단한 YAML front-matter). PyYAML은 optional → 없으면
  최소 파서라도. **frontmatter는 FTS 색인 대상**(name·tags·description) — RUNTIME_DESIGN §3.
- `read(name)`, `write(skill)`, `list()`, `read_history(name)`.

`usage.py` 책임 — `.usage.json` 사이드카(`<name>.usage.json`, 포맷 정본 §3):
`{skill, use, view, patch, last_used_at, created_at}`. 증가 시점:
- `use` += 1: **턴 종료 시 주입됐던 스킬**에(T4 observe에서). `view`: `GET /skills/{id}`
  (= `get_skill` 호출 시). `patch`: 본문 수정 승인 시(T3).

`SkillSummary`/`SkillDetail` 매핑: store + usage를 합쳐 DTO로 변환.

수용: 스킬 1개를 write→read 라운드트립, frontmatter 보존, usage 카운터 증감, history 적재.

---

## T3 — 생성·상태전이·개선(patch) (lifecycle.py, patch.py, curator/curator.py) · 정본 LEARNING_LOOP §4·§5

### 생성 (§4)
- **별도 분류 로직 없음.** 시스템 프롬프트 가이드 문구로 처리(이미 `service.prefetch`
  스텁이 `_SKILL_GUIDANCE`를 주입한다 — T4에서 유지/보강). 즉 너는 "분류기"를
  만들지 않는다.
- 생성은 **HITL** — 사용자 확인 후 저장(§4). ⚠️ **계약 주의:** CONTRACTS의 제어
  이벤트는 `tool_approval_required`·`skill_patch_proposed` **둘뿐**이다. 스킬 *생성*
  전용 제어 이벤트는 계약에 없다. 그러니 MVP에서는 생성은 `skill_created`(스트림
  이벤트)로 **알림**하고 저장하는 경로로 구현하라(생성 HITL을 위한 새 이벤트를
  발명하지 마라). 생성 시점 판단은 보수적으로(예: tool_calls가 일정 이상이고 모델이
  명시적으로 스킬화를 의도한 경우). 불확실하면 `# NOTE(agent):` 후 안전하게 미생성.

### 상태 전이 (§5.1, curator/curator.py)
- 상태: `active → stale → archived`. 자동 규칙: `last_used_at` 경과 — **stale 3일 /
  archive 7일**(설정값 `config.learning.stale_after_days`, `archive_after_days`).
  **시간 단위를 분으로 바꾸지 마라.**
- `curate()`(수동 "스킬 정리", `POST /skills/curate`) = 즉시 규칙 적용 + 결과를
  `CurateResult(transitioned=((skill_id, new_state), ...))`로 반환. 데모 경로다.
- `set_state(skill_id, state)` = 수동 archive/activate.
- 사용량 기반 전이는 **연기**(지금은 시간 규칙 + 수동만).

### 개선 patch (§5.2) — 데모 핵심
- 원칙: **제안 → HITL 승인 → 적용**. 무단 자동 수정 **금지**.
- **너의 역할 분리(중요):** learning은 **제안을 산출**(`PatchProposal`)하고
  **승인 결정대로 적용**(`apply_patch`)만 한다. 사용자에게 묻고 기다리는 HITL
  parking은 **orchestrator가 한다**(이미 구현됨). 즉:
  - `observe()`가 patch 트리거(T1a 교정 / T1b 실패우회 / T1c 누락발견 — §5.2.2)를
    감지하면 `PatchProposal(skill_id, changelog, diff, trigger, proposed_body,
    from_version, to_version)`을 **효과 리스트에 담아 반환**(적용하지 않음).
  - orchestrator가 승인 받은 뒤 `apply_patch(skill_id, approved, edited_body)` 호출.
  - `apply_patch` 적용 절차(§5.2.4): ① 현재 본문 → `.history/v<N>.md` ② 새 본문 기록
    (`edited_body` 있으면 그것), frontmatter `version += 1`, `updated_at` 갱신
    ③ usage `patch += 1` ④ **새 version 반환**(orchestrator가 `skill_patched` 발행).
    거부/None이면 변경 없음.
- `diff`는 `types.ts`의 `SkillDiff` 모양: `{"lines":[{"type":"add|remove|context",
  "text":...}, ...]}`. 표준 `difflib`로 생성.
- 트리거 감지는 **프롬프트 가이드 기반**(분류기 만들지 마라). 보수적으로:
  명백한 교정/실패가 history에 있을 때만 제안. 애매하면 미제안.

### history / rollback
- `history(skill_id)` → `.history/`의 버전들을 `SkillVersion(version, updated_at, body)`로.
- `rollback(skill_id, version)` → 해당 history 본문을 현재로 복원(현재본은 다시
  history로), version 규칙은 보수적으로 처리(불확실하면 NOTE).

수용: v1 생성 → observe가 교정 감지 → PatchProposal 반환 → apply_patch(approve)
→ v2 기록 + .history/v1.md 존재 + usage.patch==1 + 새 version 반환. curate가 오래된
스킬을 stale/archived로 전이.

---

## T4 — Loop: prefetch + observe (loop.py, service.py 파사드 결선) · 정본 LEARNING_LOOP §1·§6·§7

`prefetch(session_id, user_message) -> PrefetchResult`:
- **활성 스킬 전부 주입(inject-all-active-under-budget, §7):** `state=active`인 스킬
  본문을 `config.learning.skill_inject_token_budget` 한도 내에서 전부. 예산 초과
  시에만 `use` 상위 + FTS 랭킹으로 선별(이 구간이 retrieval 취약점 — 진단은 eval의
  `skill_hit`). 토큰 추정은 대략(len/4 등) 해도 됨, 정밀할 필요 없음.
- **메모리:** 최근 facts N개 + 쿼리 FTS 매칭 상위 K개(T1).
- **가이드 문구**: 스킬 생성 가이드(§4)를 system_prompt_additions에 포함.
- 반환: `PrefetchResult(system_prompt_additions=<렌더링된 텍스트>, skills=(InjectedSkill...),
  memories=(MemoryItem...))`. `InjectedSkill`/`MemoryItem`은 `core/types.py`.

`observe(outcome: TurnOutcome) -> list[LearningEffect]`:
- 입력 `TurnOutcome`(core/types): `final_message, history, tool_calls_made,
  injected_skill_ids, aborted`.
- 할 일: ① 주입됐던 스킬(`injected_skill_ids`) usage `use += 1` & `last_used_at`
  갱신 ② 새 fact/preference 기록 판단 → 기록 시 `MemoryWritten` 효과 ③ 스킬 생성
  판단 → 생성 시 저장 + `SkillCreated` 효과 ④ patch 트리거 판단 → `PatchProposal`
  효과(미적용). 효과 리스트로 반환(orchestrator가 SSE로 변환·HITL 처리).
- **판단은 전부 보수적**으로(데모 안정성 > 적극성). 애매하면 효과 없음.

수용: 스킬 주입된 턴 후 observe가 use 카운터를 올린다. fact가 감지되면
MemoryWritten 효과 + `list_memories()`에 반영.

---

## T5 — User model (user_model/profile.py) · 정본 LEARNING_LOOP §6, RUNTIME_DESIGN §3

- preferences(답변 스타일: 간결/상세/기술형, 선호 모델/도구)를 학습·조회.
- 저장은 `memories(kind='preference')` 재사용으로 충분(별도 테이블 불필요).
- prefetch에서 선호를 system_prompt_additions에 반영(예: "사용자는 간결한 답변 선호").
- 가장 단순하게. 데모 가시성 위주. 불확실하면 최소 구현 + NOTE.

---

## 하지 말 것 (이 모듈 특히)
- 임베딩/벡터/시맨틱 검색 추가 금지(FTS5만).
- 스킬 *생성/patch 전용 새 SSE 이벤트·엔드포인트* 발명 금지(계약 고정).
- 본문 무단 자동 수정 금지(반드시 제안→승인→적용).
- `observe`에서 사용자에게 직접 묻기/대기 금지(HITL parking은 orchestrator 소관).
- 자동 전이 시간 단위를 데모 편의로 분으로 바꾸기 금지.

## 수용 기준 종합 (최소 테스트)
1. memory: add→list→FTS 검색.
2. skill store: write→read 라운드트립(frontmatter 보존) + usage 증감 + history.
3. patch: v1→PatchProposal→apply_patch(approve)→v2 + .history/v1 + patch 카운터.
4. curator: 오래된 스킬 stale/archived 전이.
5. prefetch: active 스킬 전부 주입(예산 내) + 가이드 문구 포함.
6. **walking_skeleton.py 가 여전히 동작**(echo로 turn → done). 실제로 실행해 확인.
