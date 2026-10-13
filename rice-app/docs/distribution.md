# RICE 데스크톱 배포

설치 대상 PC에는 Node.js와 Python이 필요하지 않다. 빌드할 때 PyInstaller가
`rice-runtime`과 Python 3.13 인터프리터, FastAPI/Uvicorn, Anthropic, OS keyring
백엔드를 one-folder 실행 파일로 만들고, electron-builder가 이를 앱의
`resources/rice-runtime`에 넣는다.

번들에 포함되는 서버는 `rice-runtime`입니다. 마켓을 사용하려면 별도
`rice-platform`을 실행해야 합니다. SaaS 모델 등록 키는 사용자 데이터의
`state.db`에 저장하고, Google 커넥터 OAuth 자격증명은 OS 키체인을 사용합니다.

## 로컬에서 설치 파일 만들기

빌드 PC에는 Node.js 20+와 Python 3.13+가 필요하다.

```bash
cd rice-app
npm ci
npm run package:mac       # macOS에서
npm run package:win       # Windows에서
npm run package:linux     # Linux에서
```

산출물은 `rice-app/release/<version>/`에 생성된다. 각 명령은 다음을 한 번에
수행한다.

1. 현재 OS/CPU용 독립 실행 `rice-runtime` 빌드
2. 임시 `RICE_HOME`으로 `/health`와 정상 종료 smoke test
3. TypeScript 검사와 renderer/main/preload 빌드
4. 현재 아키텍처용 DMG/ZIP, NSIS/ZIP 또는 AppImage/DEB 생성

PyInstaller 산출물은 OS와 CPU에 종속된다. Windows 설치 파일은 Windows에서,
macOS 설치 파일은 macOS에서 만들어야 한다. `package:win`을 macOS에서 호출하면
잘못된 Python 바이너리를 섞지 않도록 즉시 실패한다. Intel과 ARM도 각각 해당
아키텍처 runner에서 빌드한다.

빌드용 Python을 명시하려면 다음 환경 변수를 사용한다.

```bash
RICE_BUILD_PYTHON=/absolute/path/to/python3.13 npm run package
```

## 모든 플랫폼 CI 빌드

`.github/workflows/desktop-release.yml`은 태그(`v*`) 또는 수동 실행 시 macOS,
Windows, Linux의 x64/arm64 runner에서 각각 네이티브 설치 파일을 만들고 workflow
artifact로 보관한다. ARM Windows/Linux runner는 GitHub의 public preview
가용성에 영향을 받을 수 있다.

## 서명과 공증

코드 서명 인증서는 저장소에 넣지 않는다. GitHub Actions repository secrets에
아래 값을 설정하면 electron-builder가 빌드 중 사용한다.

- macOS 서명: `MAC_CSC_LINK`, `MAC_CSC_KEY_PASSWORD`
- Windows 서명: `WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD`
- macOS 공증: `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`
  또는 권장 API key 방식의 `APPLE_API_KEY`, `APPLE_API_KEY_ID`,
  `APPLE_API_ISSUER`, `APPLE_TEAM_ID`

인증서 없이도 설치 산출물과 런타임 smoke test는 만들어지지만, 외부 사용자에게
배포할 최종 macOS/Windows 릴리스는 개발자 인증서로 서명해야 OS 경고를 피할 수
있다. 인증서와 Apple/Windows 개발자 계정은 코드로 대신 만들 수 없는 배포자
소유 자격증명이다.

## 설치 후 런타임 위치와 데이터

- 번들(읽기 전용): `process.resourcesPath/rice-runtime/`
- 사용자 데이터(쓰기 가능): Electron `userData/runtime/`
- 개발 모드: 기존 `rice-runtime/.venv` 또는 시스템 Python fallback

앱은 빈 localhost 포트와 일회성 bearer token을 만들고 런타임을 시작한다. 토큰은
로그에 출력하지 않는다. 종료 시 Electron의 첫 quit를 잠시 막고 Python 자식
프로세스가 종료된 뒤 앱을 끝내므로 백그라운드 프로세스가 남지 않는다.

## 빠른 진단

런타임 번들만 다시 만들고 검증하려면:

```bash
cd rice-app
npm run prepare:runtime
```

성공 시 마지막 줄에 `ready and healthy`와 실행 파일 경로가 출력된다. 결과 폴더는
`rice-app/runtime-dist/<platform>-<arch>/rice-runtime/`이며, 빌드 전용 가상환경은
`rice-app/.runtime-build/` 아래에 있어 앱에는 포함되지 않는다.
