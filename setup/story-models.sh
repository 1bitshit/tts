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
CONTEXT="${STORY_MODEL_CONTEXT:-32768}"
TTL="${STORY_MODEL_TTL_SECONDS:-180}"

find_lms() {
  command -v lms 2>/dev/null || true
  [ -x "$HOME/.lmstudio/bin/lms" ] && echo "$HOME/.lmstudio/bin/lms"
}

LMS="$(find_lms | head -n1)"
[ -n "$LMS" ] || { echo "lms not installed" >&2; exit 1; }
model_for_role() {
  case "$1" in
    author) printf '%s:%s\n' "$AUTHOR_MODEL" "$AUTHOR_QUANT" ;;
    editor) printf '%s:%s\n' "$EDITOR_MODEL" "$EDITOR_QUANT" ;;
    *) echo "role must be author or editor" >&2; exit 2 ;;
  esac
}

download_role() {
  local spec
  spec="$(model_for_role "$1")"
  "$LMS" get "$spec"
}

load_role() {
  local spec identifier gpu
  spec="$(model_for_role "$1")"
  identifier="story-$1"
  if [ "$1" = "author" ]; then
    gpu="${STORY_AUTHOR_GPU_OFFLOAD:-max}"
  else
    gpu="${STORY_EDITOR_GPU_OFFLOAD:-0.65}"
  fi
  "$LMS" unload --all >/dev/null 2>&1 || true
  "$LMS" load "$spec" --identifier "$identifier" --gpu "$gpu" \
    --context-length "$CONTEXT" --parallel 1 --ttl "$TTL" --yes
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
