# rice-app

RICE의 React UI와 Electron 데스크톱 프로세스입니다. 로컬 런타임의 HTTP/SSE
응답을 화면에 표시하고, 모델·대화·스킬·커넥터·마켓을 관리합니다.

## 개발 환경

전체 설치와 실행은 [저장소 README](../README.md)를 참고하세요.
프론트엔드 의존성은 이 디렉토리에서 `npm ci`로 설치합니다.
라이브러리와 버전은 [package.json](package.json) 및 잠금 파일이 기준입니다.

저장소 루트에서:

```bash
./app.sh    # Electron과 마켓 서버 실행; Electron이 런타임 시작
./dev.sh    # 브라우저 UI, 런타임, 마켓 서버 실행
```

UI만 실행하려면 이 디렉토리에서:

```bash
npx vite --config vite.renderer.config.ts   # http://localhost:5273
```

브라우저 UI는 `VITE_RICE_RUNTIME_URL` 또는 기본 `http://localhost:8080`에
연결합니다. 토큰이 필요한 런타임에는 `VITE_RICE_RUNTIME_TOKEN`도 지정합니다.
Electron은 main process가 시작한 런타임의 포트·토큰을 preload IPC로 전달받습니다.
대화와 도구 실행은 두 환경 모두 localhost HTTP/SSE로 통신합니다.

런타임이 없으면 연결 안내와 재연결 버튼을 표시합니다. 대화 전송·커넥터 연결을
막고 입력 중인 내용은 보존합니다. 런타임이 시작된 뒤 재연결하면 저장된 모델,
대화와 커넥터를 다시 불러옵니다.

## 구조

```text
rice-app/
├── electron/                  # 창·런타임 관리와 OS 기능 IPC
├── src/
│   ├── App.tsx                # 연결 초기화·라우팅·온보딩
│   ├── screens/               # 대화·스킬·마켓·커넥터·설정
│   ├── components/workspace/  # 기능별 공용 UI
│   ├── components/ui/         # 버튼·모달·아이콘·접근성
│   ├── lib/                   # API/SSE·상태·번역·파일 뷰어
│   ├── locales/               # 한국어·영어·일본어
│   └── styles/globals.css     # 테마·디자인 토큰
├── scripts/                   # 런타임 번들 및 설치 파일 빌드
└── package.json
```

`lib/mockData.ts`에는 기본 모델 설정과 마켓 카테고리 코드가 있습니다.
`lib/learningData.ts`는 UI 타입·변환 함수·예시 데이터 등을 포함합니다.
스킬·메모리·설치 상태는 연결된 런타임에서, 마켓 목록은 플랫폼에서 가져옵니다.

## 설정과 데이터

| 항목 | 저장·적용 경로 |
|---|---|
| 테마·글꼴 | `rice.settings` → 문서의 테마·글꼴 속성 |
| 언어 | i18n의 `rice.language` 저장소 |
| 모델·온도·Ollama 주소·추론 표시 | `PUT /settings` → 로컬 런타임 설정 |
| SaaS 등록 모델·키 | 로컬 `state.db`; UI에는 마스킹된 키 반환 |
| 대화·스킬·메모리 | 로컬 런타임의 데이터 저장소 |
| 패널 크기 등 UI 상태 | `rice.*` localStorage 항목 |

일반 설정 화면에 별도로 있는 Ollama 주소와 자동 시작·사용자 모델링·로컬 저장·
텔레메트리 옵션은 현재 localStorage에 저장만 됩니다. 이 값으로 런타임 동작을
제어하지 않습니다. 모델 접속 주소는 채팅의 모델 설정을 사용합니다.

API 키는 기기에 보관하고 선택한 모델 제공자에 요청할 때 인증에 사용합니다.
SaaS 요청에 포함된 대화·도구 결과도 해당 제공자에 전달됩니다.

## 확인

이 디렉토리에서 실행합니다.

```bash
npm run type-check
npm run lint
npm run build:react
```

`build:react`는 현재 Vite 설정에 따라 렌더러와 Electron main/preload를 빌드합니다.
결과는 `dist/`와 `dist-electron/`에 있습니다.

## 설치 파일

```bash
npm run package:mac
npm run package:win
npm run package:linux
```

각 명령은 대상 OS에서 실행합니다. Python 런타임 준비·타입 검사·앱 빌드·패키징을
함께 수행하고 `release/<version>/`에 설치 파일을 생성합니다.
[배포 문서](docs/distribution.md)에 런타임 번들·서명·진단 방법이 있습니다.

## UI 개발 기준

테마·색·타입은 [디자인 시스템](docs/DESIGN_SYSTEM.md)의 토큰을 사용합니다.
공용 모달과 포커스 처리는 `components/ui` 및 `hooks/useDialogA11y.ts`를 사용하며,
사용자 문구는 [번역 카탈로그](docs/i18n.md)에서 관리합니다.

연결 문제는 개발 런타임의 `/health` 응답과 앱의 연결 안내를 먼저 확인합니다.
로컬 모델 문제는 Ollama 실행 상태와 채팅의 모델 설정을 확인합니다.
