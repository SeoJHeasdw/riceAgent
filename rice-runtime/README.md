# rice-runtime

RICE의 로컬 Python 런타임입니다. 대화 이력, 모델 스트리밍, 실행 계획과 도구 호출,
스킬·메모리·학습 제안, MCP 연결을 한 프로세스에서 처리합니다.

## 설치와 실행

Python 3.13 이상이 필요합니다. 이 디렉토리에서:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[claude,connectors,dev]'
.venv/bin/rice-runtime --host 127.0.0.1 --port 8080 --token 'local-dev-token'
```

위 가상환경 경로는 macOS/Linux 기준이며 Windows에서는 `Scripts/`를 사용합니다.
로컬 모델만 사용하면 `claude` 추가 의존성을 생략할 수 있습니다.

`GET /health`는 토큰 없이 확인할 수 있습니다. 토큰을 지정한 런타임의 나머지
API 요청에는 `Authorization: Bearer <token>` 헤더가 필요합니다.
로컬 개발에서는 `--token ''`로 인증을 끌 수 있으며, 저장소의 `dev.sh`가 이 방식으로
실행합니다. Electron은 자동으로 빈 포트와 일회성 인증 토큰을 지정합니다.

## 구현된 기능

| 모듈 | 역할 |
|---|---|
| `agent/` | 계획·도구 실행 루프, 모델 어댑터, 컨텍스트, 백그라운드 작업 |
| `learning/` | 스킬·메모리 저장, 학습 제안·승인, 리뷰·정리·후보 평가 |
| `connector/` | OAuth, 원격·로컬 MCP, 도구 정책·카탈로그 |
| `execution/` | 플러그인 로딩·설치와 리소스 |
| `server/` | HTTP/SSE API, 세션·승인, 구성 요소 연결 |
| `core/` | 설정·경로·저장소·이벤트·공용 타입 |

`server/composition_root.py`에서 구성 요소를 연결합니다. HTTP API와 SSE 형식은
[contracts/](contracts/)와 [API 계약](../RICE_CONTRACTS.md)에 정의되어 있습니다.

## 모델과 키

- **Ollama**: 로컬 서버의 설치된 모델을 사용합니다.
- **Anthropic**: Claude API 어댑터를 사용합니다. `claude` 추가 의존성이 필요합니다.
- **OpenAI / OpenAI 호환**: 등록한 모델·API 키·선택적 커스텀 주소를 사용합니다.
- **echo**: 초기 설정과 오프라인 테스트용 공급자입니다. 실제 모델의 추론 결과가 아닙니다.

앱에서 등록한 SaaS 키와 커스텀 주소는 `state.db`의 `provider_models`에 저장합니다.
키는 평문으로 보관되며 `GET /settings` 응답에서는 마스킹합니다. 선택한 SaaS
제공자에 요청할 때 해당 키를 인증에 사용합니다. Claude의 기존 환경변수·키체인
키 로딩 경로도 남아 있습니다. Google 커넥터의 OAuth 자격증명은 OS 키체인에
저장하며 SaaS 모델 등록 키와 저장 경로가 다릅니다.

## 데이터 위치

기본 데이터 루트는 `~/.rice/`이며 `RICE_HOME`으로 변경할 수 있습니다.
Electron 패키지에서는 supervisor가 앱 사용자 데이터 디렉토리의 `runtime/`를
데이터 루트로 지정합니다.

```text
<RICE_HOME>/
├── config.yaml   # 모델·실행·학습 설정
├── state.db      # 대화·메모리·모델 등록 등
├── skills/       # 스킬 파일
├── plugins/      # 설치 플러그인
├── connectors/   # 커넥터 메타데이터
├── .mcp.json     # 로컬 MCP 정의
├── workspace/    # 기본 생성 파일 위치
└── logs/
```

파일 도구의 작업 폴더는 앱에서 바꿀 수 있습니다. `terminal` 도구는 호스트 셸을
실행하므로 파일 도구의 작업 폴더 제한과 같은 경계를 제공하지 않습니다.

## 확인

```bash
.venv/bin/python -m pytest
.venv/bin/python walking_skeleton.py
```

`walking_skeleton.py`는 echo 공급자로 이벤트와 저장 흐름을 확인하는 오프라인
진단입니다. 모델 품질이나 실제 외부 도구 연결을 검증하지 않습니다.

평가 하네스는 `eval/`에 있습니다. `GET /eval/impact`는 저장된 평가 리포트를
반환하며, 저장소에 포함된 기본 `latest.json`은 `model: scripted`인 테스트 결과입니다.
실제 모델의 성능 결과와 구분해 사용해야 합니다.

모듈 지도와 설계 배경은 [런타임 구성](../RICE_RUNTIME_DIRECTORY.md),
[실행 모델](../RICE_AGENT_EXECUTION.md), [학습 루프](../RICE_LEARNING_LOOP_DESIGN.md)에 있습니다.
