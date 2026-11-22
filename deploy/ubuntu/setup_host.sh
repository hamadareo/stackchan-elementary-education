#!/usr/bin/env bash
# 新しい Ubuntu PC に、StackChan のサーバー一式を自動で入れる(Mac から実行)。
#   bash deploy/ubuntu/setup_host.sh <ssh の宛先> <機器名の頭(例: Lat)> [コピー元(既定: 環境変数 SC_SERVER)]
# 前提: Mac から宛先へ公開鍵で SSH できること。宛先で一度だけ `sudo loginctl enable-linger $USER` と
#       `sudo usermod -aG dialout $USER`(その後 再起動)を実行しておくと、再起動後も自動で動き、USB も使える。
# 内容: コピー元の ~/stackchan(声・翻訳モデル・聞き取りモデル・ブリッジ)と uv・arduino-cli を Mac 経由で配る
#       → Python 3.12 の環境を作る → systemd(声・翻訳・ブリッジ)を登録して起動 → 管理アプリ(Qt)と自動起動を登録。
# 聞き取りと翻訳は、その PC の CPU で動く(s109 のトンネルは入れない)。
set -euo pipefail
T="${1:?宛先(例: user@host や ssh の別名)}"; NAME="${2:?機器名の頭(例: Lat)}"; SRC="${3:-${SC_SERVER:?コピー元を第 3 引数で渡すか、環境変数 SC_SERVER=user@host を設定してください}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "[1/5] 配布(約 5.5GB。数十分かかります)"
ssh "$SRC" 'cd ~ && tar c --exclude=stackchan/.venv --exclude=stackchan/.venv-gui --exclude=stackchan/logs --exclude=stackchan/bridge/logs --exclude=__pycache__ --exclude=stackchan/llm/server.log --exclude=stackchan/xcblib stackchan .local/bin/uv .local/bin/uvx bin/arduino-cli .arduino15' \
  | ssh "$T" 'mkdir -p ~/.local/bin ~/bin && cd ~ && tar x'
echo "[2/5] ブリッジを最新にする"
rsync -a --exclude .venv --exclude logs --exclude __pycache__ --exclude .ctl --exclude calib.json "$ROOT/bridge/" "$T:stackchan/bridge/"
rsync -a --exclude __pycache__ --exclude stackchan.png --exclude run.sh "$ROOT/app/" "$T:stackchan/app/"
rsync -a "$ROOT/arduino/" "$T:stackchan/arduino/"
ssh "$T" 'mkdir -p ~/stackchan/units'; rsync -a "$ROOT"/deploy/ubuntu/sc-bridge.service "$ROOT"/deploy/ubuntu/sc-llm.service "$ROOT"/deploy/ubuntu/sc-voicevox.service "$T:stackchan/units/"
scp -q "$ROOT/deploy/ubuntu/requirements-server.txt" "$T:/tmp/reqs.txt"
echo "[3/5] Python 環境とサービス"
ssh "$T" "NAME='$NAME' bash -s" <<'REMOTE'
set -e
export PATH="$HOME/.local/bin:$PATH"; cd ~/stackchan
chmod +x ~/.local/bin/uv ~/.local/bin/uvx ~/bin/arduino-cli s109_link.sh llm/llama-b12302/llama-server voicevox/linux-cpu-x64/run 2>/dev/null || true
uv venv --python 3.12 .venv >/dev/null && uv pip install --python .venv/bin/python -r /tmp/reqs.txt | tail -1
uv venv --python 3.12 .venv-gui >/dev/null && uv pip install --python .venv-gui/bin/python pyside6-essentials==6.12.0 | tail -1
mkdir -p ~/.config/systemd/user
for u in sc-bridge sc-llm sc-voicevox; do cp units/$u.service ~/.config/systemd/user/; done
sed -i "s/SC_DEVICE_NAME=[A-Za-z0-9_-]*/SC_DEVICE_NAME=$NAME/; s/^After=.*/After=sc-voicevox.service sc-llm.service/" ~/.config/systemd/user/sc-bridge.service
sed -i "s/-t 12/-t 6/; s/--cpu_num_threads 8/--cpu_num_threads 6/" ~/.config/systemd/user/sc-llm.service ~/.config/systemd/user/sc-voicevox.service
systemctl --user daemon-reload
systemctl --user enable --now sc-voicevox sc-llm sc-bridge
REMOTE
echo "[4/5] 管理アプリ(アイコン・ドック・自動起動)"
ssh "$T" 'bash ~/stackchan/app/install.sh | tail -1'
echo "[5/5] 確認"
sleep 20; ssh "$T" 'systemctl --user is-active sc-voicevox sc-llm sc-bridge; curl -s -m5 localhost:8080/api/state | head -c 80; echo'
echo "完了。Qt の管理アプリが画面に出ない(libxcb-cursor0 が無い X11 環境)ときは、README の手順を見てください。"
