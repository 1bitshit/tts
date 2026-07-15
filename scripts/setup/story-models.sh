#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
[ -f .env ] && while IFS='=' read -r key value; do
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  value="${value%\"}"; value="${value#\"}"
  [ -n "${!key+x}" ] || export "$key=$value"
done < .env
LMS="${LMS_BIN:-$HOME/.lmstudio/bin/lms}"
[ -x "$LMS" ] || { echo "lms fehlt; zuerst ./setup/lmstudio.sh install" >&2; exit 1; }
MODELS="${STORY_SETUP_MODELS:-Qwen/Qwen3-1.7B-GGUF}"
IFS=',' read -ra items <<< "$MODELS"
for model in "${items[@]}"; do
  [[ "$model" == http* ]] || model="https://huggingface.co/$model"
  "$LMS" get "$model" --gguf --yes
done
