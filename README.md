# RICE 설계 문서

> RICE: 사용자 경험을 축적하며 학습·진화하는 개인 에이전트 + 내부 마켓 생태계 (IBM 내부용 데스크톱 앱)
> Claude Desktop · Hermes Agent를 벤치마킹한 애플리케이션

## 로컬 실행 (개발)

```bash
./dev.sh            # 백엔드(:8080) + 프론트(:5273) 한방 실행. Ctrl-C로 함께 종료
```

→ 브라우저에서 http://localhost:5273 열고 채팅 한 줄 보내면 end-to-end(렌더러→런타임→ollama qwen) 확인.
모델 호출은 **Ollama 데몬(:11434)** 이 떠 있어야 함 (`ollama serve`, `ollama list` 로 `qwen3.5:9b-mlx` 확인).

수동으로 따로 띄우려면:

```bash
# 백엔드 (런타임 :8080)
cd rice-runtime && source .venv/bin/activate && rice-runtime --port 8080 --token ""

# 마켓 플랫폼 (rice-platform :7070) — 없어도 앱 동작, 마켓 카탈로그만 빈 상태
cd rice-platform && python3 -m rice_platform --port 7070 --artifacts-dir ./artifacts
# venv가 있으면:
cd rice-platform && source .venv/bin/activate && rice-platform --port 7070 --artifacts-dir ./artifacts

# 프론트 (※ npm run dev 아님 — electron 플러그인이 크래시함)
cd rice-app && npx vite --config vite.renderer.config.ts
```

> 렌더러 단독 dev는 브라우저라 런타임을 **cross-origin**으로 호출 → 런타임이 localhost CORS를
> 허용한다([server/app.py](rice-runtime/src/rice_runtime/server/app.py)). Electron 빌드는 IPC라 무관.

## 문서 구성 (읽는 순서)

| 순서 | 문서 | 성격 | 상태 |
|------|------|------|------|
| 1 | [RICE_PRODUCT_IDEA.md](RICE_PRODUCT_IDEA.md) | 제품 컨셉 (source of truth) | ✅ 안정 |
| 2 | [RICE_REPO_STRATEGY.md](RICE_REPO_STRATEGY.md) | 레포 구조 + 레포 간 계약 원칙 | ✅ 안정 |
| 3 | [RICE_RUNTIME_DIRECTORY.md](RICE_RUNTIME_DIRECTORY.md) | rice-runtime 모듈 구조 (정본) | ✅ 안정 |
| 4 | [RICE_RUNTIME_DESIGN.md](RICE_RUNTIME_DESIGN.md) | 런타임 설계 + 패키징 + 보안 | ✅ 확정 |
| 5 | [RICE_LEARNING_LOOP_DESIGN.md](RICE_LEARNING_LOOP_DESIGN.md) | learning loop 자체 스펙 (SKILL.md·usage·생성·개선patch·curator·memory) | ✅ 확정 |
| 6 | [RICE_LEARNING_EVAL.md](RICE_LEARNING_EVAL.md) | 학습 측정·증명 (cold/warm ablation·eval harness·Learning Impact 패널) | ✅ 확정 |
| 7 | [RICE_CONTRACTS.md](RICE_CONTRACTS.md) | app↔runtime API + artifact + platform API | ✅ 확정 |
| 8 | [RICE_PLATFORM_DESIGN.md](RICE_PLATFORM_DESIGN.md) | rice-platform 설계 (catalog·registry·publish) | ✅ 확정 (Phase 2) |
| 참조 | [HERMES_LEARNING_LOOP_REFERENCE.md](HERMES_LEARNING_LOOP_REFERENCE.md) | Hermes Agent 코드 분석 (설계 문서 아님) | ✅ 검증 완료 |

## 문서 간 역할 규칙 (중복 방지)

- 제품 차원 정의(화면 컨셉, 범위)는 **PRODUCT_IDEA**에만 쓴다. (엔드포인트·계약은 쓰지 않는다)
- 레포 구조와 분리 전략은 **REPO_STRATEGY**에만 쓴다.
- 모듈 구조·디렉토리·핵심 불변식·화면→모듈 매핑은 **RUNTIME_DIRECTORY**가 단일 출처다.
- SSE 이벤트·엔드포인트·artifact manifest 계약은 **CONTRACTS**가 단일 출처다.
- learning loop 자체 스펙(SKILL.md 포맷·usage·생성·개선patch·curator·memory)은 **LEARNING_LOOP_DESIGN**이 단일 출처다.
- 학습이 일어났음의 **측정·증명**(지표·cold/warm ablation·eval harness·Learning Impact 패널 데이터 계약)은 **LEARNING_EVAL**이 단일 출처다.
- rice-platform 설계(catalog·registry·publish)는 **PLATFORM_DESIGN**이 단일 출처다.
- 모든 설계 결정은 확정되어 각 문서 본문에 평문으로 반영돼 있다 (별도 결정 등록부 없음).
- **HERMES_LEARNING_LOOP_REFERENCE**는 읽기 전용 참조 자료다.