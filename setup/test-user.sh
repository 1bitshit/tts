#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
USERNAME="${1:-bkg}"

if [ -z "${BEAM_API_KEY:-}" ]; then
  echo "BEAM_API_KEY is required and is never committed." >&2
  echo "Usage: BEAM_API_KEY='<key from beam.eysho.info>' $0 [username]" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
fi

if command -v openssl >/dev/null 2>&1; then
  TTS_KEY="tts_$(openssl rand -hex 24)"
else
  TTS_KEY="tts_$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
fi

set_value() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^" key "=" { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$ENV_FILE" > "$temporary"
  mv "$temporary" "$ENV_FILE"
}

set_value API_KEYS "$TTS_KEY"
set_value BEAM_USERNAME "$USERNAME"
set_value BEAM_API_KEY "$BEAM_API_KEY"
set_value BEAM_DOMAIN "${BEAM_DOMAIN:-beam.eysho.info}"
chmod 600 "$ENV_FILE"

cat <<EOF
Testzugang wurde lokal in .env eingerichtet.

Benutzer/Tag: $USERNAME
TTS API-Key:  $TTS_KEY

Der TTS API-Key wird nur dieses eine Mal vollständig angezeigt.
Starte jetzt:
  ./run.sh --with-lms --with-beam

Qwen: https://${USERNAME}-8000me-up80.${BEAM_DOMAIN:-beam.eysho.info}
LM:   https://${USERNAME}-1235me-up80.${BEAM_DOMAIN:-beam.eysho.info}
EOF
