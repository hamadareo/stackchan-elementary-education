#!/usr/bin/env bash
# StackChan ブリッジの起動。s109 への転送(耳12346・翻訳12345)は Sokutsu のスクリプトで貼る(冪等)。
# s109 に届かなければ翻訳・耳はこのMacに自動で落ちる。
#   bash bridge/run.sh [--engine auto|s109|mac] [--port 8765]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="${TSUYAKU_REPO:-/Users/work/tsuyaku}"
bash "$TS/scripts/voicevox_local.sh" || true
bash "$TS/scripts/s109_tunnel.sh" || echo "[run] s109 に届かないのでこのMacで動かします"
export TSUYAKU_SRC="$TS/src"
export PYTHONUNBUFFERED=1
PY="$ROOT/bridge/.venv/bin/python"; BR="$ROOT/bridge/stackchan_bridge.py"
if [ "${SC_SUPERVISE:-0}" = "1" ]; then      # 無人運転向け: 落ちたら 3 秒後に再起動(Ctrl-C で止まる)
  mkdir -p "$ROOT/bridge/logs"
  while true; do "$PY" "$BR" "$@" 2>&1 | tee -a "$ROOT/bridge/logs/bridge-$(date +%F).log"; rc=${PIPESTATUS[0]}; [ $rc -eq 0 ] || [ $rc -eq 130 ] && break; echo "[run] ブリッジが終了(code $rc)。3秒後に再起動"; sleep 3; done
else
  mkdir -p "$ROOT/bridge/logs"
  # 画面にも出しつつ、日付ごとのログにも残す(git 管理外)。tools/metrics.py で集計できる
  "$PY" "$BR" "$@" 2>&1 | tee -a "$ROOT/bridge/logs/bridge-$(date +%F).log"
fi
