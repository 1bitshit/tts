#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p .runtime/qwen .runtime/rust-tts logs

if [ -f .env ]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# || -z "$key" ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%\"}"; value="${value#\"}"
    [ -n "${!key+x}" ] || export "$key=$value"
  done < .env
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
PID_FILE="$ROOT/.runtime/qwen/api.pid"
LOG_FILE="$ROOT/logs/qwen-api.log"

find_python() {
  for candidate in \
    "${QWEN_TTS_PYTHON:-}" \
    "${CONDA_PREFIX:-}/bin/python" \
    "$ROOT/.venv/bin/python" \
    /notebooks/fakeroot/home/bkg/miniconda/envs/qwen-tts/bin/python \
    /notebooks/workspace/bkg/miniconda/envs/qwen-tts/bin/python \
    "$HOME/miniconda3/envs/qwen-tts/bin/python" \
    "$HOME/miniconda/envs/qwen-tts/bin/python"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ] && \
       "$candidate" -c 'import fastapi, uvicorn, httpx, pydantic_settings' >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done
  if command -v python >/dev/null 2>&1 && python -c 'import fastapi, uvicorn, httpx, pydantic_settings' >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "No usable qwen-tts Python environment found." >&2
  return 1
}

stop_api() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  else
    local old_pid
    old_pid="$(pgrep -f '[u]vicorn app.main:app' | head -1 || true)"
    [ -z "$old_pid" ] || kill -TERM "$old_pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

echo "Building WebUI..."
if [ ! -d frontend/node_modules ]; then (cd frontend && npm install); fi
(cd frontend && npm run build)

if [ "${TTS_ENGINE:-rust-server}" = "rust-server" ]; then
  echo "Restarting C reference engine and Rust TTS service..."
  bash setup/tts-engine.sh restart
  bash setup/rust-engine.sh restart
elif [ "${TTS_ENGINE:-rust-server}" = "c-server" ]; then
  echo "Restarting pure-C TTS engine..."
  bash setup/tts-engine.sh restart
fi

echo "Restarting FastAPI..."
stop_api
PYTHON_BIN="$(find_python)"
if command -v setsid >/dev/null 2>&1; then
  nohup setsid "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    </dev/null >"$LOG_FILE" 2>&1 &
else
  nohup "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    </dev/null >"$LOG_FILE" 2>&1 &
fi
echo "$!" > "$PID_FILE"

for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/health"
echo
curl -fsS "http://127.0.0.1:$PORT/health/tts"
echo
echo "Restart complete. Beam, LM Studio and SSH were not restarted."
