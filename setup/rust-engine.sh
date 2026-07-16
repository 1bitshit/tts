#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"
LISTEN="${RUST_TTS_LISTEN:-127.0.0.1:8030}"
UPSTREAM="${RUST_TTS_UPSTREAM:-http://127.0.0.1:8020}"
PID_FILE="$ROOT/.runtime/rust-tts/server.pid"
LOG_FILE="$ROOT/logs/rust-tts.log"
BIN="$ROOT/rust-engine/target/release/qwen3-tts-server-rs"
mkdir -p "$(dirname "$PID_FILE")" "$ROOT/logs"
running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  kill -0 "$pid" 2>/dev/null && grep -q 'qwen3-tts-server-rs' "/proc/$pid/cmdline" 2>/dev/null
}
build() { (cd "$ROOT/rust-engine" && cargo build --release -p qwen3-tts-server-rs); }
stop() {
  local pid=""
  running && pid="$(cat "$PID_FILE")"
  [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null && break
    sleep 1
  done
  [ -z "$pid" ] || kill -KILL "$pid" 2>/dev/null || true
  local port="${LISTEN##*:}"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
  fi
  rm -f "$PID_FILE"
}
start() {
  [ -x "$BIN" ] || build
  if running; then echo "Rust TTS already running (PID $(cat "$PID_FILE"))."; return; fi
  rm -f "$PID_FILE"
  local port="${LISTEN##*:}"
  if command -v fuser >/dev/null 2>&1 && fuser "${port}/tcp" >/dev/null 2>&1; then
    echo "Port $port is already occupied by another process." >&2
    return 1
  fi
  nohup "$BIN" --listen "$LISTEN" --upstream "$UPSTREAM" \
    --max-concurrent "${RUST_TTS_MAX_CONCURRENT:-1}" \
    --connect-timeout-seconds "${RUST_TTS_CONNECT_TIMEOUT_SECONDS:-5}" \
    --request-timeout-seconds "${RUST_TTS_REQUEST_TIMEOUT_SECONDS:-600}" \
    </dev/null >"$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  local health="http://${LISTEN}/health/ready"
  for _ in $(seq 1 60); do curl -fsS "$health" >/dev/null 2>&1 && { echo "Rust TTS: http://$LISTEN"; return; }; running || break; sleep 1; done
  tail -n 100 "$LOG_FILE" >&2 || true
  return 1
}
case "$ACTION" in
  build) build ;;
  start) start ;;
  stop) stop ;;
  restart) stop; build; start ;;
  status) running && { echo "Rust TTS running (PID $(cat "$PID_FILE"))"; curl -fsS "http://${LISTEN}/health/ready"; echo; } || echo "Rust TTS stopped" ;;
  *) echo "Usage: $0 {build|start|stop|restart|status}" >&2; exit 2 ;;
esac
