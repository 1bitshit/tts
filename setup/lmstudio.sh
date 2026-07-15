#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-start}"
RUNTIME_DIR="$ROOT/.runtime/lmstudio"
LOG_DIR="$ROOT/logs"
PROXY_PID_FILE="$RUNTIME_DIR/proxy.pid"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

load_env() {
  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    chmod 600 "$ROOT/.env"
    echo "Created $ROOT/.env; configure API_KEYS before production use." >&2
  fi
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# || -z "$key" ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%\"}"; value="${value#\"}"
    if [ -z "${!key+x}" ]; then export "$key=$value"; fi
  done < "$ROOT/.env"
}

load_env
# Authentication is enforced by app.lms_proxy with API_KEYS. An unrelated
# inherited LM_API_TOKEN can prevent llmster from booting when it is not a
# native sk-lm-* token, so never pass it to the loopback-only daemon.
unset LM_API_TOKEN
INTERNAL_HOST="${LM_STUDIO_INTERNAL_HOST:-127.0.0.1}"
INTERNAL_PORT="${LM_STUDIO_INTERNAL_PORT:-1234}"
PROXY_HOST="${LM_STUDIO_PROXY_HOST:-0.0.0.0}"
PROXY_PORT="${LM_STUDIO_PROXY_PORT:-1235}"

find_python() {
  if [ -n "${QWEN_TTS_PYTHON:-}" ] && [ -x "$QWEN_TTS_PYTHON" ]; then echo "$QWEN_TTS_PYTHON"; return; fi
  for candidate in \
    "$ROOT/.venv/bin/python" \
    "/notebooks/fakeroot/home/bkg/miniconda/envs/qwen-tts/bin/python" \
    "$HOME/miniconda/envs/qwen-tts/bin/python" \
    "$HOME/.conda/envs/qwen-tts/bin/python"; do
    if [ -x "$candidate" ]; then echo "$candidate"; return; fi
  done
  if command -v python >/dev/null 2>&1 && python -c 'import uvicorn, httpx' >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "No Python environment with uvicorn/httpx found. Activate qwen-tts first." >&2
  return 1
}

find_lms() {
  if command -v lms >/dev/null 2>&1; then command -v lms; return; fi
  for candidate in "$HOME/.lmstudio/bin/lms" /notebooks/fakeroot/home/bkg/.lmstudio/bin/lms; do
    if [ -x "$candidate" ]; then echo "$candidate"; return; fi
  done
  return 1
}

install_lms() {
  if find_lms >/dev/null 2>&1; then return; fi
  echo "Installing official LM Studio llmster..."
  curl -fsSL https://lmstudio.ai/install.sh | bash
}

proxy_running() {
  [ -f "$PROXY_PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PROXY_PID_FILE")"
  kill -0 "$pid" 2>/dev/null && grep -q "app.lms_proxy:app" "/proc/$pid/cmdline" 2>/dev/null
}

stop_proxy() {
  if proxy_running; then
    local pid
    pid="$(cat "$PROXY_PID_FILE")"
    kill -TERM "$pid"
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PROXY_PID_FILE"
}

start_proxy() {
  if proxy_running; then echo "LM auth proxy already running (PID $(cat "$PROXY_PID_FILE"))."; return; fi
  local python_bin
  python_bin="$(find_python)"
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$python_bin" -m uvicorn app.lms_proxy:app --host "$PROXY_HOST" --port "$PROXY_PORT" \
      </dev/null >"$LOG_DIR/lmstudio-proxy.log" 2>&1 &
  else
    nohup "$python_bin" -m uvicorn app.lms_proxy:app --host "$PROXY_HOST" --port "$PROXY_PORT" \
      </dev/null >"$LOG_DIR/lmstudio-proxy.log" 2>&1 &
  fi
  echo "$!" > "$PROXY_PID_FILE"
}

start_all() {
  install_lms
  local lms
  lms="$(find_lms)"
  "$lms" daemon up
  "$lms" server stop >/dev/null 2>&1 || true
  "$lms" server start --port "$INTERNAL_PORT" --bind "$INTERNAL_HOST"

  if [ -n "${LM_STUDIO_MODEL:-}" ]; then
    if [ "${LM_STUDIO_DOWNLOAD_MODEL:-false}" = "true" ]; then "$lms" get "$LM_STUDIO_MODEL"; fi
    "$lms" load "$LM_STUDIO_MODEL" --yes
  fi

  start_proxy
  for _ in $(seq 1 30); do
    if curl -fsS "http://$INTERNAL_HOST:$INTERNAL_PORT/v1/models" >/dev/null 2>&1; then break; fi
    sleep 1
  done
  curl -fsS "http://$INTERNAL_HOST:$INTERNAL_PORT/v1/models" >/dev/null
  local proxy_key="${API_KEYS%%,*}"
  proxy_key="${proxy_key#${proxy_key%%[![:space:]]*}}"
  proxy_key="${proxy_key%${proxy_key##*[![:space:]]}}"
  for _ in $(seq 1 30); do
    if [ -n "$proxy_key" ]; then
      curl -fsS -H "X-API-Key: $proxy_key" "http://127.0.0.1:$PROXY_PORT/v1/models" >/dev/null 2>&1 && break
    elif [ "${ENV:-development}" != "production" ]; then
      curl -fsS "http://127.0.0.1:$PROXY_PORT/v1/models" >/dev/null 2>&1 && break
    fi
    sleep 1
  done
  if [ -n "$proxy_key" ]; then
    curl -fsS -H "X-API-Key: $proxy_key" "http://127.0.0.1:$PROXY_PORT/v1/models" >/dev/null
  elif [ "${ENV:-development}" != "production" ]; then
    curl -fsS "http://127.0.0.1:$PROXY_PORT/v1/models" >/dev/null
  else
    echo "API_KEYS must be configured when ENV=production." >&2
    return 1
  fi
  echo "LM Studio internal: http://$INTERNAL_HOST:$INTERNAL_PORT"
  echo "LM Studio API:      http://$PROXY_HOST:$PROXY_PORT"
  echo "Auth: same API_KEYS as Qwen TTS (X-API-Key or Bearer token)"
}

stop_all() {
  stop_proxy
  if lms="$(find_lms 2>/dev/null)"; then "$lms" server stop >/dev/null 2>&1 || true; fi
  echo "LM Studio and auth proxy stopped."
}

show_status() {
  if lms="$(find_lms 2>/dev/null)"; then "$lms" server status || true; else echo "lms: not installed"; fi
  if proxy_running; then echo "auth proxy: running on $PROXY_HOST:$PROXY_PORT (PID $(cat "$PROXY_PID_FILE"))"; else echo "auth proxy: stopped"; fi
}

case "$ACTION" in
  install) install_lms ;;
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) show_status ;;
  *) echo "Usage: $0 {install|start|stop|restart|status}" >&2; exit 2 ;;
esac
