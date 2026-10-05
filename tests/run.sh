#!/usr/bin/env bash
# Build stage-<N>/, start it under the grading limits, run every acceptance suite 1..N.
# Usage: tests/run.sh <N> [extra pytest args, e.g. -k moves]
# Set TK_BASE_URL to test an already running service instead of building one.
set -euo pipefail
N="${1:?usage: tests/run.sh <stage> [pytest args]}"; shift || true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${TK_PYTHON:-/Users/arseniy/hack/band_my/ref/.venv/bin/python}"
SUITES=()
for i in $(seq 1 "$N"); do
  [ -d "$ROOT/tests/stage-$i" ] && SUITES+=("$ROOT/tests/stage-$i")
done

cleanup() { [ -n "${CID:-}" ] && docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ -z "${TK_BASE_URL:-}" ]; then
  TAG="tk-accept-stage-$N"
  docker build -q -t "$TAG" "$ROOT/stage-$N" >/dev/null
  PORT=$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
  CID=$(docker run -d --cpus 2 --memory 2g -e PORT=8080 -p "127.0.0.1:$PORT:8080" "$TAG")
  export TK_BASE_URL="http://127.0.0.1:$PORT"
  export TK_CONTAINER="$CID"
  ok=""
  for _ in $(seq 1 120); do
    if curl -fsS "$TK_BASE_URL/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 0.5
  done
  [ -n "$ok" ] || { echo "service never became healthy"; docker logs "$CID" | tail -40; exit 1; }
fi

cd "$ROOT/tests"
"$PY" -m pytest -q -p no:cacheprovider --import-mode=importlib --rootdir "$ROOT/tests" \
  "${SUITES[@]}" "$@"
