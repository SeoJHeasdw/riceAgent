#!/usr/bin/env bash
# rice-platform 개발 서버 실행
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${RICE_PLATFORM_PORT:-7070}"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MARKETPLACE_DIR="${RICE_MARKETPLACE_DIR:-$REPO_ROOT/../rice-marketplace}"

# 가상환경 없으면 생성
if [ ! -d ".venv" ]; then
  echo "[rice-platform] creating venv..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# 의존성 설치
pip install -e . -q

if [ -d "$MARKETPLACE_DIR/artifacts" ]; then
  export RICE_ARTIFACTS_DIR="$MARKETPLACE_DIR/artifacts"
fi
if [ -f "$MARKETPLACE_DIR/.claude-plugin/marketplace.json" ]; then
  export RICE_PLUGIN_MARKETPLACES="$MARKETPLACE_DIR"
fi

ARTIFACTS_DIR="${RICE_ARTIFACTS_DIR:-$(pwd)/artifacts}"

echo "[rice-platform] starting on http://localhost:$PORT"
echo "[rice-platform] artifacts dir: $ARTIFACTS_DIR"
if [ -n "${RICE_PLUGIN_MARKETPLACES:-}" ]; then
  echo "[rice-platform] plugin marketplaces: $RICE_PLUGIN_MARKETPLACES"
fi
echo ""

exec rice-platform --host 0.0.0.0 --port "$PORT" --artifacts-dir "$ARTIFACTS_DIR"
