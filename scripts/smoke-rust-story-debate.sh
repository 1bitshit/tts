#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUST_URL="${RUST_TTS_URL:-http://127.0.0.1:8030}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsS "$RUST_URL/health/ready" >/dev/null
render() {
  local name="$1" text="$2" speaker="$3"
  curl -fsS -X POST "$RUST_URL/api/v1/engine/speech" \
    -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"text":sys.argv[1],"speaker":sys.argv[2],"language":"German","rate":1.0}))' "$text" "$speaker")" \
    -o "$TMP/$name.wav"
  python3 - "$TMP/$name.wav" <<'PY'
import sys, wave
p=sys.argv[1]
with wave.open(p,'rb') as w:
    assert w.getnchannels()==1
    assert w.getsampwidth()==2
    assert w.getframerate()==24000
    assert w.getnframes()>2400
    print(f"{p}: {w.getnframes()/w.getframerate():.2f}s, 24kHz mono PCM")
PY
}
render story '[calm] Der Regen fiel leise. [pause:300ms] [fear] Hinter ihr krachte plötzlich eine Tür! [relieved] Es war nur der Wind.' vivian
render debate '[confident] Die Fakten sind eindeutig. [pause:250ms] [angry] Dieses Gegenargument ignoriert die Daten! [thoughtful] Prüfen wir die Zahlen noch einmal.' ryan
echo 'Rust Story/Debate emotion smoke test passed.'
