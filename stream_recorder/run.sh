#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Stream Recorder starten. Die App schreibt ihr Log nur in die eigene Logdatei
# (/data/log/stream-recorder.log, Ansicht „Log“ in der App), nicht ins Home-Assistant-Log.
set -e
cd /app
exec python3 -u /app/stream_recorder.py
