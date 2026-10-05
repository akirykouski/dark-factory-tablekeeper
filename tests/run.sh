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

PREV_CIDS=()
cleanup() {
  [ -n "${CID:-}" ] && docker rm -f "$CID" >/dev/null 2>&1 || true
  for c in "${PREV_CIDS[@]:-}"; do [ -n "$c" ] && docker rm -f "$c" >/dev/null 2>&1 || true; done
}

start() {  # start <folder> <tag>; prints "<cid> <url>" once healthy
  docker build -q -t "$2" "$1" >/dev/null
  local port cid
  port=$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
  cid=$(docker run -d --cpus 2 --memory 2g -e PORT=8080 -p "127.0.0.1:$port:8080" "$2")
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
      echo "$cid http://127.0.0.1:$port"; return 0; fi
    sleep 0.5
  done
  echo "service in $1 never became healthy" >&2; docker logs "$cid" | tail -40 >&2
  docker rm -f "$cid" >/dev/null; return 1
}
trap cleanup EXIT

if [ -z "${TK_BASE_URL:-}" ]; then
  read -r CID TK_BASE_URL < <(start "$ROOT/stage-$N" "tk-accept-stage-$N")
  export TK_BASE_URL
fi
# Earlier stages' builds, for upgrade (export -> import) tests: TK_PREV_URL_<i>.
if [ "$N" -gt 1 ] && [ -z "${TK_NO_PREV:-}" ]; then
  for i in $(seq 1 $((N - 1))); do
    if [ -d "$ROOT/stage-$i" ]; then
      read -r pc pu < <(start "$ROOT/stage-$i" "tk-accept-stage-$i")
      PREV_CIDS+=("$pc"); export "TK_PREV_URL_$i=$pu"
    fi
  done
fi

export TK_STAGE="$N"
cd "$ROOT/tests"
"$PY" -m pytest -q -p no:cacheprovider --import-mode=importlib --rootdir "$ROOT/tests" \
  "${SUITES[@]}" "$@"
