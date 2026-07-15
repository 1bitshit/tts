#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"
BIN="$ROOT/bin/beam"
RUNTIME_DIR="$ROOT/.runtime/beam"
LOG_DIR="$ROOT/logs"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

load_env() {
  [ -f "$ROOT/.env" ] || { echo "Missing $ROOT/.env" >&2; return 1; }
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# || -z "$key" ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%\"}"; value="${value#\"}"
    [ -n "${!key+x}" ] || export "$key=$value"
  done < "$ROOT/.env"
}

load_env
BEAM_USERNAME="${BEAM_USERNAME:-}"
BEAM_API_KEY="${BEAM_API_KEY:-}"
BEAM_DOMAIN="${BEAM_DOMAIN:-beam.eysho.info}"
BEAM_CONTROL_PORT="${BEAM_CONTROL_PORT:-8080}"
TTS_LOCAL_PORT="${PORT:-8000}"
LMS_LOCAL_PORT="${LM_STUDIO_PROXY_PORT:-1235}"
TTS_REMOTE_PORT="${BEAM_TTS_REMOTE_PORT:-80}"
LMS_REMOTE_PORT="${BEAM_LMS_REMOTE_PORT:-80}"
SSH_ENABLED="${BEAM_SSH_ENABLED:-false}"
SSH_LOCAL_PORT="${BEAM_SSH_LOCAL_PORT:-22}"
SSH_REMOTE_PORT="${BEAM_SSH_REMOTE_PORT:-22}"

running() {
  local name="$1" pid_file="$RUNTIME_DIR/$1.pid" pid
  [ -f "$pid_file" ] || return 1
  pid="$(cat "$pid_file")"
  kill -0 "$pid" 2>/dev/null && grep -q "$BIN" "/proc/$pid/cmdline" 2>/dev/null
}

start_tunnel() {
  local name="$1" local_port="$2" remote_port="$3" pid_file="$RUNTIME_DIR/$1.pid"
  if running "$name"; then
    echo "$name tunnel already running (PID $(cat "$pid_file"))."
    return
  fi
  nohup setsid "$BIN" \
    --username "$BEAM_USERNAME" --api-key "$BEAM_API_KEY" \
    --server "$BEAM_DOMAIN" --server-port "$BEAM_CONTROL_PORT" --debug --undead \
    "$local_port:me" "up:$remote_port" \
    </dev/null >"$LOG_DIR/beam-$name.log" 2>&1 &
  echo "$!" > "$pid_file"
  sleep 1
  if ! running "$name"; then
    echo "Beam $name tunnel failed. Log:" >&2
    tail -n 60 "$LOG_DIR/beam-$name.log" >&2 || true
    rm -f "$pid_file"
    return 1
  fi
  echo "$name: http://${BEAM_USERNAME}-${local_port}me-up${remote_port}.${BEAM_DOMAIN}"
}

stop_tunnel() {
  local name="$1" pid_file="$RUNTIME_DIR/$1.pid" pid
  if running "$name"; then
    pid="$(cat "$pid_file")"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

start_all() {
  [ -n "$BEAM_USERNAME" ] || { echo "Set BEAM_USERNAME in .env" >&2; return 1; }
  [ -n "$BEAM_API_KEY" ] || { echo "Set BEAM_API_KEY in .env" >&2; return 1; }
  [ -x "$BIN" ] || { echo "Beam client missing or not executable: $BIN" >&2; return 1; }
  start_tunnel qwen "$TTS_LOCAL_PORT" "$TTS_REMOTE_PORT"
  start_tunnel lms "$LMS_LOCAL_PORT" "$LMS_REMOTE_PORT"
  if [ "$SSH_ENABLED" = "true" ]; then
    start_tunnel ssh "$SSH_LOCAL_PORT" "$SSH_REMOTE_PORT"
  fi
  echo "Beam uses the API keys configured in the protected local services."
}

status_all() {
  for name in qwen lms ssh; do
    if running "$name"; then
      echo "$name: running (PID $(cat "$RUNTIME_DIR/$name.pid"))"
    else
      echo "$name: stopped"
    fi
  done
}

case "$ACTION" in
  start) start_all ;;
  stop) stop_tunnel qwen; stop_tunnel lms; stop_tunnel ssh ;;
  restart) stop_tunnel qwen; stop_tunnel lms; stop_tunnel ssh; start_all ;;
  status) status_all ;;
  *) echo "Usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
