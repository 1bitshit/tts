#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"
ENGINE_HOME="${C_TTS_HOME:-$ROOT/data/c-tts}"
SOURCE_DIR="$ENGINE_HOME/source"
MODEL_DIR="${C_TTS_MODEL_DIR:-$ENGINE_HOME/qwen3-tts-1.7b}"
PORT="${C_TTS_PORT:-8020}"
RUNTIME_DIR="$ROOT/.runtime/c-tts"
PID_FILE="$RUNTIME_DIR/server.pid"
LOG_FILE="$ROOT/logs/c-tts.log"
mkdir -p "$ENGINE_HOME" "$RUNTIME_DIR" "$ROOT/logs"

running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  kill -0 "$pid" 2>/dev/null && grep -q 'qwen_tts' "/proc/$pid/cmdline" 2>/dev/null
}

install_engine() {
  if [ ! -d "$SOURCE_DIR/.git" ]; then
    git clone --depth 1 https://github.com/gabriele-mastrapasqua/qwen3-tts.git "$SOURCE_DIR"
  else
    git -C "$SOURCE_DIR" pull --ff-only
  fi

  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential libopenblas-dev curl git
  fi

  if command -v nvcc >/dev/null 2>&1; then
    make -C "$SOURCE_DIR" cuda
    echo cuda > "$ENGINE_HOME/backend"
  else
    echo "nvcc not found; building the OpenBLAS CPU backend." >&2
    make -C "$SOURCE_DIR" blas
    echo cpu > "$ENGINE_HOME/backend"
  fi

  "$SOURCE_DIR/download_model.sh" --model large --dir "$MODEL_DIR"
  bash "$SOURCE_DIR/download_assets.sh" --no-voices
}

stop_engine() {
  if running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill -TERM "$pid"
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

start_engine() {
  if running; then
    echo "C TTS engine already running (PID $(cat "$PID_FILE"))."
    return
  fi
  [ -x "$SOURCE_DIR/qwen_tts" ] || { echo "C TTS engine is not installed. Run: $0 install" >&2; return 1; }
  [ -f "$MODEL_DIR/model.safetensors" ] || { echo "C TTS model is missing. Run: $0 install" >&2; return 1; }

  local backend=()
  if [ "$(cat "$ENGINE_HOME/backend" 2>/dev/null || echo cpu)" = "cuda" ]; then
    backend=(--backend cuda --quant-mixed)
  else
    backend=(--int8)
  fi
  (
    cd "$SOURCE_DIR"
    QWEN_CUDA_FUSED_TALKER=1 QWEN_CUDA_CONVDEC=1 \
      nohup ./qwen_tts -d "$MODEL_DIR" "${backend[@]}" --serve "$PORT" --workers 1 \
      </dev/null >"$LOG_FILE" 2>&1 &
    echo "$!" > "$PID_FILE"
  )

  for _ in $(seq 1 120); do
    curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1 && {
      echo "C TTS engine: http://127.0.0.1:$PORT"
      return
    }
    running || break
    sleep 1
  done
  echo "C TTS engine failed to become healthy. Log:" >&2
  tail -n 100 "$LOG_FILE" >&2 || true
  return 1
}

status_engine() {
  if running; then
    echo "C TTS engine: running (PID $(cat "$PID_FILE"))"
    curl -fsS "http://127.0.0.1:$PORT/v1/health" || true
    echo
  else
    echo "C TTS engine: stopped"
  fi
}

case "$ACTION" in
  install) install_engine ;;
  start) start_engine ;;
  stop) stop_engine ;;
  restart) stop_engine; start_engine ;;
  status) status_engine ;;
  *) echo "Usage: $0 {install|start|stop|restart|status}" >&2; exit 2 ;;
esac
