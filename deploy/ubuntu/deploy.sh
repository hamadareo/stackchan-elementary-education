#!/usr/bin/env bash
# Mac から Ubuntu サーバーへ、ブリッジと管理画面を配って再起動する(更新・指示はこの Mac から)。
#   bash deploy/ubuntu/deploy.sh [user@host]      既定: 環境変数 SC_SERVER
# 初回のセットアップ(uv・Python環境・VOICEVOX・llama.cpp・モデル)は SETUP.md の「Ubuntu サーバー」を参照。
set -euo pipefail
U="${1:-${SC_SERVER:?宛先を引数で渡すか、環境変数 SC_SERVER=user@host を設定してください}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
rsync -a --exclude .venv --exclude logs --exclude __pycache__ --exclude .ctl --exclude calib.json "$ROOT/bridge/" "$U:stackchan/bridge/"
rsync -a --exclude __pycache__ --exclude stackchan.png --exclude run.sh "$ROOT/app/" "$U:stackchan/app/"
ssh "$U" 'bash ~/stackchan/app/install.sh' || echo "(管理アプリの登録は、GUI 環境 ~/stackchan/.venv-gui が要ります)"
ssh "$U" 'bash ~/stackchan/bridge/fetch_models.sh >/dev/null' || echo "(顔の識別モデルを取得できませんでした)"
rsync -a "$ROOT/arduino/" "$U:stackchan/arduino/"
rsync -a "$ROOT/deploy/ubuntu/s109_link.sh" "$U:stackchan/"
mkdir -p "$ROOT/firmware/.pio/build/m5stack-cores3" 2>/dev/null; ssh "$U" "mkdir -p stackchan/firmware"; [ -f "$ROOT/firmware/.pio/build/m5stack-cores3/firmware.factory.bin" ] && rsync -a "$ROOT/firmware/.pio/build/m5stack-cores3/firmware.factory.bin" "$U:stackchan/firmware/stackchan-factory.bin"
ssh "$U" 'systemctl --user daemon-reload; systemctl --user restart sc-bridge; sleep 3; systemctl --user is-active sc-bridge sc-s109 sc-llm sc-voicevox'
echo "管理画面: http://${U#*@}:8080/"
