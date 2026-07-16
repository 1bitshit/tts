#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ACTION="${1:-status}"
ROLE="${2:-}"
AUTHOR_MODEL="${STORY_AUTHOR_MODEL:-Qwen/Qwen3-14B-GGUF}"
EDITOR_MODEL="${STORY_EDITOR_MODEL:-bartowski/Mistral-Small-24B-Instruct-2501-GGUF}"
AUTHOR_QUANT="${STORY_AUTHOR_QUANT:-Q4_K_M}"
EDITOR_QUANT="${STORY_EDITOR_QUANT:-Q4_K_M}"
AUTHOR_LOAD_ID="${STORY_AUTHOR_LOAD_ID:-qwen3-14b}"
EDITOR_LOAD_ID="${STORY_EDITOR_LOAD_ID:-mistral-small-24b-instruct-2501}"
AUTHOR_CONTEXT="${STORY_AUTHOR_CONTEXT:-16384}"
EDITOR_CONTEXT="${STORY_EDITOR_CONTEXT:-24576}"
TTL="${STORY_MODEL_TTL_SECONDS:-180}"

find_lms() {
  command -v lms 2>/dev/null || true
  [ -x "$HOME/.lmstudio/bin/lms" ] && echo "$HOME/.lmstudio/bin/lms"
}

LMS="$(find_lms | head -n1)"
[ -n "$LMS" ] || { echo "lms not installed" >&2; exit 1; }
model_for_role() {
  case "$1" in
    author) printf '%s@%s\n' "$AUTHOR_MODEL" "$AUTHOR_QUANT" ;;
    editor) printf '%s@%s\n' "$EDITOR_MODEL" "$EDITOR_QUANT" ;;
    *) echo "role must be author or editor" >&2; exit 2 ;;
  esac
}

download_role() {
  local spec
  spec="$(model_for_role "$1")"
  "$LMS" get --gguf --yes "$spec"
}

load_once() {
  local spec="$1" identifier="$2" context="$3" gpu="$4"
  local args=(load "$spec" --identifier "$identifier" --context-length "$context" --parallel 1 --ttl "$TTL")
  [ -z "$gpu" ] || args+=(--gpu "$gpu")
  "$LMS" "${args[@]}"
}

load_role() {
  local spec identifier requested_gpu context
  if [ "$1" = "author" ]; then
    spec="$AUTHOR_LOAD_ID"
    requested_gpu="${STORY_AUTHOR_GPU_OFFLOAD:-max}"
    context="$AUTHOR_CONTEXT"
  else
    spec="$EDITOR_LOAD_ID"
    requested_gpu="${STORY_EDITOR_GPU_OFFLOAD:-max}"
    context="$EDITOR_CONTEXT"
  fi
  identifier="story-$1"
  "$LMS" unload --all >/dev/null 2>&1 || true

  if load_once "$spec" "$identifier" "$context" "$requested_gpu"; then
    echo "$identifier"
    return
  fi

  echo "Primary load failed; retrying with automatic GPU placement." >&2
  "$LMS" unload --all >/dev/null 2>&1 || true
  if load_once "$spec" "$identifier" "$context" ""; then
    echo "$identifier"
    return
  fi

  echo "Automatic load failed; retrying with 8192 context and 80% GPU." >&2
  "$LMS" unload --all >/dev/null 2>&1 || true
  load_once "$spec" "$identifier" 8192 0.8
  echo "$identifier"
}

case "$ACTION" in
  download)
    [ -n "$ROLE" ] && download_role "$ROLE" || { download_role author; download_role editor; }
    ;;
  load)
    [ -n "$ROLE" ] || { echo "Usage: $0 load {author|editor}" >&2; exit 2; }
    load_role "$ROLE"
    ;;
  unload)
    "$LMS" unload --all
    ;;
  status)
    "$LMS" ps || true
    ;;
  *)
    echo "Usage: $0 {download [author|editor]|load {author|editor}|unload|status}" >&2
    exit 2
    ;;
esac
