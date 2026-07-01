#!/usr/bin/env bash
#
# RICE 로컬 Electron 앱 실행 (데스크탑 애플리케이션 모드).
#
# 띄우는 것: vite-plugin-electron 이 포함된 `vite.config.ts` 로 Vite 한 개만 실행.
#   - Vite 가 빌드 완료 후 vite-plugin-electron 이 Electron 을 자동 spawn.
#   - Electron RuntimeManager 가 rice-runtime 을 내부에서 spawn.
#   → 프로세스를 직접 두 개 띄우지 않아도 된다.
# Ollama(:11434)는 별도 데몬 — 아래에서 떠 있는지만 확인.
#
#   ./app.sh
#
# Ctrl-C 한 번이면 모두 함께 종료된다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT/rice-runtime"
APP_DIR="$ROOT/rice-app"
PLATFORM_PORT="${RICE_PLATFORM_PORT:-7070}"
PLATFORM_DIR="$ROOT/rice-platform"

# 이 스크립트의 프로세스 그룹 전체를 SIGKILL — 손자 프로세스(Electron, rice-runtime)까지 포함.
cleanup() {
  trap - INT TERM EXIT
  kill -9 -$$ 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# ── 0) Ollama 떠 있나 ───────────────────────────────────────────────────────
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "⚠️  Ollama(:11434)가 응답하지 않습니다. 'ollama serve' 로 먼저 띄워주세요."
  echo "    (채팅 시 모델 호출이 실패합니다. UI/연결 확인만이라면 무시해도 됨)"
fi

# ── 1) rice-platform (마켓 API, :7070) ──────────────────────────────────────
if [ -d "$PLATFORM_DIR" ]; then
  echo "▶ 플랫폼: rice-platform --port $PLATFORM_PORT"
  (
    cd "$PLATFORM_DIR"
    if [ -x ".venv/bin/rice-platform" ]; then
      exec .venv/bin/rice-platform --port "$PLATFORM_PORT" --artifacts-dir ./artifacts
    else
      exec python3 -m rice_platform --port "$PLATFORM_PORT" --artifacts-dir ./artifacts
    fi
  ) &
  
fi

# ── 2) Vite (vite-plugin-electron 이 Electron 까지 자동 기동) ────────────────
echo "▶ Vite + Electron 기동 중..."
(
  cd "$APP_DIR"
  export NODE_ENV=development
  export RICE_RUNTIME_DIR="$RUNTIME_DIR"
  exec npx vite --config vite.config.ts
) &


echo ""
echo "✅ Vite 시작됨 — Electron 창이 곧 열립니다."
echo "   Ctrl-C 하면 모두 함께 정리됩니다."
wait
