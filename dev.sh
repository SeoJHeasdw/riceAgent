#!/usr/bin/env bash
#
# RICE 로컬 개발 한방 실행 (백엔드 + 프론트).
#
# 띄우는 것: ① Python 런타임(rice-runtime, :8080)  ② React 렌더러(vite, :5273)
# Ollama(:11434)는 별도 데몬이라 여기서 안 띄움 — 아래에서 떠 있는지만 확인.
#
#   ./dev.sh
#
# Ctrl-C 한 번이면 둘 다 같이 종료된다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$ROOT/rice-runtime"
APP_DIR="$ROOT/rice-app"
PORT="${RICE_PORT:-8080}"
PLATFORM_PORT="${RICE_PLATFORM_PORT:-7070}"
PLATFORM_DIR="$ROOT/rice-platform"

# 이 스크립트의 프로세스 그룹 전체를 SIGKILL — 손자 프로세스(uvicorn worker 등)까지 포함.
cleanup() {
  trap - INT TERM EXIT
  kill -9 -$$ 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# ── 0) Ollama 떠 있나 (모델 호출 대상) ──────────────────────────────────────
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "⚠️  Ollama(:11434)가 응답하지 않습니다. 'ollama serve' 로 먼저 띄워주세요."
  echo "    (채팅 시 모델 호출이 실패합니다. UI/연결 확인만이라면 무시해도 됨)"
fi

# ── 1) Python 백엔드 ────────────────────────────────────────────────────────
echo "▶ 백엔드: rice-runtime --port $PORT (token 없음, dev)"
(
  cd "$RUNTIME_DIR"
  # venv가 있으면 그 파이썬을, 없으면 시스템 python3 사용.
  if [ -x ".venv/bin/rice-runtime" ]; then
    exec .venv/bin/rice-runtime --port "$PORT" --token ""
  else
    exec python3 -m rice_runtime --port "$PORT" --token ""
  fi
) &


# 백엔드 health 대기 (최대 ~15s).
echo -n "  health 대기"
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then echo " ✓"; break; fi
  echo -n "."; sleep 0.5
done

# ── 2) rice-platform (마켓 API, :7070) ──────────────────────────────────────
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

# ── 3) React 프론트 (electron 빼고 브라우저 전용 dev) ───────────────────────
echo "▶ 프론트: vite --config vite.renderer.config.ts → http://localhost:5273"
(
  cd "$APP_DIR"
  exec npx vite --config vite.renderer.config.ts
) &


echo ""
echo "✅ 떴습니다."
echo "   앱:       http://localhost:5273"
echo "   runtime:  http://localhost:$PORT/health"
echo "   platform: http://localhost:$PLATFORM_PORT/health"
echo "   종료: Ctrl-C (모두 함께 정리됨)"
wait
