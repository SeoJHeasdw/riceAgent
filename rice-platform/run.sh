#!/usr/bin/env bash
# rice-platform 개발 서버 실행
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${RICE_PLATFORM_PORT:-7070}"

# 가상환경 없으면 생성
if [ ! -d ".venv" ]; then
  echo "[rice-platform] creating venv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# 의존성 설치
pip install -e . -q

echo "[rice-platform] starting on http://localhost:$PORT"
echo "[rice-platform] artifacts dir: $(pwd)/artifacts"
echo ""

exec rice-platform --host 0.0.0.0 --port "$PORT" --artifacts-dir ./artifacts
