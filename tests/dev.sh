#!/bin/bash
# Lokale Entwicklungsumgebung ohne Home Assistant und ohne echte Streams:
#   Test-Server (Streams, Cover-Suche, nachgebauter Supervisor) auf 127.0.0.1:8001
#   Oberfläche wie in der HA-Seitenleiste (ohne Login):  http://127.0.0.1:8099/
#   direkter Zugriff mit Login admin / devpasswort:        http://127.0.0.1:8765/
#   Daten in .dev/data, Speicherordner .dev/media/stream-recorder
#
#   tests/dev.sh          starten (Strg+C beendet beides)
#   tests/dev.sh --fresh  Daten löschen und neu anlegen (mit Demo-Daten)
#   SR_LANG=en tests/dev.sh   Home Assistant auf Englisch
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
DEV="$ROOT/.dev"
[[ "${1:-}" == "--fresh" ]] && rm -rf "$DEV"
mkdir -p "$DEV/data" "$DEV/media/stream-recorder"
[[ -f "$DEV/data/options.json" ]] || echo '{"storage_path": "/media/stream-recorder", "username": "admin", "password": "devpasswort", "log_level": "debug"}' > "$DEV/data/options.json"

export STREAM_RECORDER_DATA_DIR="$DEV/data" STREAM_RECORDER_STORAGE="$DEV/media/stream-recorder" \
       STREAM_RECORDER_LISTEN_HOST=127.0.0.1 STREAM_RECORDER_TRUSTED_PROXY=127.0.0.1 \
       STREAM_RECORDER_SUPERVISOR_URL=http://127.0.0.1:8001 SUPERVISOR_TOKEN=dev STREAM_RECORDER_STDIN=0 \
       STREAM_RECORDER_COVER_DEEZER="http://127.0.0.1:8001/deezer/search" \
       STREAM_RECORDER_COVER_ITUNES="http://127.0.0.1:8001/itunes/search"

if [[ ! -f "$DEV/data/recorder.db" ]]; then
  "$PY" "$ROOT/tests/seed_demo_data.py" "$DEV/data/recorder.db" "$DEV/media/stream-recorder"
  echo "Demo-Daten angelegt."
fi

"$PY" "$ROOT/tests/fake_stream.py" 8001 &
FAKE=$!
trap 'kill $FAKE 2>/dev/null' EXIT
sleep 1
[[ -n "${SR_LANG:-}" ]] && curl -s -X POST -H 'content-type: application/json' -d "{\"language\":\"$SR_LANG\"}" http://127.0.0.1:8001/_ha >/dev/null
echo "Seitenleiste (Ingress): http://127.0.0.1:8099/   Direkt: http://127.0.0.1:8765/ (admin / devpasswort)"
"$PY" "$ROOT/stream_recorder/app/stream_recorder.py"
