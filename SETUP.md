# セットアップ手順(他の人が使う場合)

このロボットは「端末(StackChan)」と「ブリッジ(Mac 上のプログラム)」の 2 つで動く。
翻訳・音声認識・音声合成は Sokutsu(tsuyaku)の部品を使うため、**ブリッジは Sokutsu を導入した Mac が前提**。

## 1. 必要なもの
- M5Stack StackChan(CoreS3)と USB-C ケーブル、2.4GHz の Wi-Fi
- Mac(Sokutsu=tsuyaku 導入済み。VOICEVOX は Sokutsu のスクリプトが起動する)
- (任意)演算サーバー(GPU)。無ければ Mac だけで動く(翻訳は遅くなる)

## 2. ブリッジ(Mac)
```bash
cd bridge
python3.10 -m venv .venv && .venv/bin/pip install -r requirements.txt
# YuNet 顔検出モデルを bridge/models/ に置く(OpenCV Zoo: face_detection_yunet_2023mar.onnx)
export TSUYAKU_REPO=/path/to/tsuyaku       # 既定は /Users/work/tsuyaku
bash run.sh                                # 演算サーバーが使えれば優先、だめなら Mac
bash run.sh --engine mac                   # Mac だけで動かす
```
ブリッジは起動すると LAN に `_sokutsu-bridge._tcp`(mDNS)で自分を告知する。

## 2b. Ubuntu サーバーで動かす(Mac を使わない構成)
ブリッジ・VOICEVOX・小さな翻訳モデルを Ubuntu に常駐させ、Mac は更新と指示だけに使う。
- 構成(systemd --user、`loginctl enable-linger` で再起動後も自動): `sc-voicevox`(声)/ `sc-llm`(CPU の翻訳モデル=予備)/
  `sc-s109`(s109 の GPU へのトンネル。聞き取りと小さな翻訳モデルを s109 で動かす)/ `sc-bridge`(ブリッジ+管理画面)。
- 端末は mDNS でブリッジを探すので、サーバーの IP を端末に書かなくてよい(`BRIDGE_HOST` は予備)。
- s109 に届かなければ、聞き取り(SenseVoice)と翻訳(Qwen3-4B)は Ubuntu の CPU に自動で落ちる。
- 配る・再起動: `bash deploy/ubuntu/deploy.sh`(Mac から)。ユニットとトンネルの元は `deploy/ubuntu/`。
- 管理画面: `http://<サーバー>:8080/`(音量・言語・話題・読み上げ・端末の画面・直近の通訳)。`SC_ADMIN_TOKEN` で合言葉を付けられる。
- 初回セットアップ: uv で Python 3.12 の環境 → `websockets numpy zeroconf requests sherpa-onnx==2.0.1 opencv-python-headless==4.10.0.84 pywhispercpp espeakng-loader` →
  tsuyaku の src と models(silero・kokoro・sense-voice)を配置 → VOICEVOX linux-cpu-x64 と llama.cpp(ubuntu-x64)+ Qwen3-4B-Instruct-2507 Q4 を配置。
  PortAudio が無い場合は `deploy/ubuntu/sounddevice.py` を PYTHONPATH に置く。
- 韓国語の声は macOS の `say` 頼みのため、Ubuntu では出ない(日本語・英語・中国語は可)。

## 3. 端末(ファーム)
```bash
cd firmware
cp src/config.h.example src/config.h       # WIFI_SSID / WIFI_PASS / OTA_PASS を編集
pio run -t upload                          # 初回は USB。2 回目以降は無線: SC_OTA_PASS=... pio run -e ota -t upload --upload-port <端末のIP>
```
端末はブリッジを mDNS で自動的に見つけて接続する(見つからなければ `BRIDGE_HOST` を使う)。
つながると上部バーに「耳・訳」の処理機器(s109 / Mac)、時刻、音量、電池が出る。

## 4. 操作
- 声: 「音量を上げて/下げて/最大/ミュート」「中国語モード」「英語にして」(28 文字以内の短い発話だけコマンド扱い)
- 画面タップ: 上部バーの「音」の左=音量を下げる / 右=上げる
- 制御ファイル(開発用): `bridge/.ctl/volume_up`、`volume_down`、`shot`(画面を撮る。`SC_SNAP_DIR` 指定時)

## 5. 既知の制限
- 他人の環境で使うには、Sokutsu の導入・演算サーバーの設定が必要(ブリッジ単体の配布版は未作成)。
- Wi-Fi の設定は現状ビルド時(config.h)。端末側のセットアップ画面は未実装。
- 頭のタッチ反応は誤検出のため無効(`TOUCH_REACT 1` で有効化)。

## 2c. 複数台と、新しい機器の追加

- **同じ Wi-Fi の機器は、何台でも同時に使えます。** 機器の ID は Wi-Fi の MAC アドレス(ファームウェアが hello で名乗る)。
  Ubuntu のブリッジが台帳(`~/.sokutsu_admin` と同じ場所の `~/.sokutsu_devices.json`)に名前・言語・話題を覚えます。
- **ファームウェアを書き込み済みの機器**は、電源を入れて Wi-Fi につなぐだけ。初めて見た機器は自動で登録(名前 `StackChan-数字`)され、
  音量・明るさ・向き・表情が初期設定され、画面に「セットアップ完了」と出ます。管理画面の「機器」で名前を変えられます。
- **何もしていない新品**は、Mac に USB で挿すだけ(`bash tools/install_provision_agent.sh` で、Mac のログイン時に待ち受けを自動起動。
  解除は `--uninstall`)。待ち受けなしで手動なら `python3 tools/provision.py`(1 台)/ `--watch`。
  挿すと: MAC を読む → 登録済みならそのまま → 新品なら書き込み(入れないときは RST 長押しをお願いして自動でやり直す)
  → Wi-Fi につながって自動登録・初期設定、までを macOS の通知で知らせます。
  ※ Ubuntu から USB 書き込みするには、Ubuntu のユーザーを `dialout` グループに入れる必要があります(管理者権限が要ります)。
- **全台をまとめて更新**: `SC_OTA_PASS=… python3 tools/ota_all.py`(接続中の機器へ 1 台ずつ無線で書き込み)。
- 操作の対象は管理画面の「機器」で選びます(全機器 / 1 台)。言語・話題・音量・首・表情は機器ごとに保存されます。
  顔の登録は、選んだ機器(全機器のときは最初の 1 台)のカメラで撮ります。
- 顔の登録・手振り・マイクの診断など、画面に出る「いま見えている人」「雑音」は選んだ機器のものです。
