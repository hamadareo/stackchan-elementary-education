# StackChan × Sokutsu — 自律する通訳ロボット

StackChan(CoreS3)を「耳・口・顔・首」だけの薄い端末にし、翻訳などの重い処理は
**s109(GPU)優先・Mac フォールバック**で行う。Sokutsu(tsuyaku リポジトリ)の部品をそのまま使う。

```
StackChan(CoreS3)                      Mac(bridge)                    s109(GPU)
 マイク ─PCM16 16kHz──WebSocket──▶  VAD/区切り(Sokutsu)
 カメラ ─JPEG ~4fps───────────────▶  顔検出 → 首の目標               耳(faster-whisper) ◀─┐
                                      認識 → 文の区切り → 翻訳 ──SSH転送──▶  訳(qwen3.8-flash-next) │
 ◀── 表情 / 字幕 / 首(look) ───────  読み上げの音づくり(Kokoro/say)    届かなければ Mac 完結 ──┘
 ◀── 読み上げ PCM ───────────────
 スピーカー・顔(avatar)・首サーボ
```

自律行動(ボタン不要・USB を外しても Wi-Fi で継続): 人の顔を見つけて首を向ける / いないときは見回す / 聞こえた言語を自動判定して
もう一方の言語へ訳して読み上げる / 考え中は「?」顔、訳し終えたら笑顔 / 自分の声は聞かない(半二重)。

## 状態(2026-11-11)
実機(CoreS3 + StackChan)で、USB・Wi-Fi の両方で動作確認済み。
日本語⇄英語の通訳(ずんだもん声・話速自動調整・文ごとのストリーミング再生)、顔追従(YuNet)、見回し、
話しながらの身ぶり、電池・ゆすり検知、OTA 書き込み、mDNS によるブリッジ自動発見、会話ログ保存まで動く。

## 自立動作(2026-11-11)
- 通信は **USB と Wi-Fi の両対応**。端末は hello_ack をくれた側を使い、USB を外しても Wi-Fi で続く。
  `firmware/src/config.h` の `WIFI_SSID`/`WIFI_PASS` を設定して書き込むと有効(空なら USB のみ)。
  ブリッジは USB と WebSocket(8765)を同時に待ち受ける(`--serial` を付けた場合)。
- 電池(INA226)を30秒ごとに Mac へ通知。15%未満で字幕と声で知らせる。
- 持ち上げ・ゆすりを IMU で検知してびっくりする(サーボ自身の揺れは無視)。
- 頭のてっぺんタッチでくすぐったがる / 90秒無音で眠る / 人が見えない間は見回す。
- **顔追従**: 端末のカメラ(約3枚/秒)を Mac の YuNet で検出し、首を向ける。初回に首を動かして
  画像のずれから向きと量を自動校正(`bridge/calib.json`。カメラ位置を変えたら削除して再校正)。
- **ブリッジの自動発見**: ブリッジが mDNS(`_sokutsu-bridge._tcp`)で告知し、端末が探して接続する。
  `config.h` の BRIDGE_HOST は見つからないときの予備。
- **会話ログ**: `bridge/logs/日付.jsonl` に原文・訳を追記(この Mac のみ・git 管理外。`SC_NO_LOG=1` で停止)。
- **内容に合わせた反応**: 「ありがとう」「ごめん」などで話し終わりに笑顔・悲しい顔になる。
- **上部ステータスバー**: 時刻 / 耳・訳を処理している機器(s109 か Mac。切断中は赤で「切断」)/ 音量 / 電池(充電中は「充」)。
- **音量調節**: 声(「音量を上げて/下げて/最大/ミュート」)、バーの「音」の左右をタップ、`bridge/.ctl/volume_up|down`。設定は電源を切っても残る。
- **言語切替**: 声で「中国語モード」「英語にして」「韓国語にして」(28 文字以内の短い発話のみ。再接続しても保持)。
- **画面の自動調整**: 暗い部屋では画面を暗く、眠っている間はさらに暗くする。字幕は 14 秒で消える。
- **無人運転の保険**: `SC_SUPERVISE=1 bash bridge/run.sh` で落ちても再起動。端末は 15 分切断が続くと自己再起動。
- **開発用**: `bridge/.ctl/shot`(画面を撮って `SC_SNAP_DIR/screen.jpg` に保存)、`bridge/.ctl/say.txt`(1行目=言語、以降=読み上げる文)、`tools/metrics.py`(ログ集計)、`tools/summary.py`(会話ログ集計)、`tests/`(単体試験)。
- あいさつは廃止(`SC_GREET_BOOT=1` で起動時に一度、`SC_GREET_MIN=<分>` で定期)。頭タッチ反応は誤検出のため無効(`TOUCH_REACT 1` で復活)。
- Mac のログイン時にブリッジを自動起動: `bash deploy/install_launchd.sh install`(解除は uninstall)。

## 速さ(2026-11-11)
訳しながら文単位で「音づくり→送信→再生」を重ねる。訳し始めから最初の音まで約 0.8〜1.4 秒(長い一文は読点で先に読む)。
端末は読み上げ用の2面バッファで、再生中に次の文を受け取って続けて鳴らす。転送で欠けた文は端末が知らせて1回再送する。

## 使い方
### 1. Mac のブリッジ
```bash
bash bridge/run.sh                  # s109 への転送を貼り、s109優先で起動(届かなければMac完結)
bash bridge/run.sh --engine mac     # Mac だけで動かす
bridge/.venv/bin/pip install --no-deps opencv-python-headless==4.10.0.84 zeroconf   # 顔追従・自動発見(bridge/models に YuNet が必要)
```
実機なしの確認: `bridge/.venv/bin/python bridge/sim_client.py 入力16kHz.wav --pair ja-en --out reply.wav`

### 2. StackChan のファーム
```bash
cd firmware
cp src/config.h.example src/config.h      # Wi-Fi・Mac の LAN アドレス・サーボのピンを編集
pio run -t upload                          # CoreS3 を USB で接続
```
**初回は `SERVO_ENABLE 0`** で顔・音声・字幕だけ確認し、そのあとサーボのピンと可動域を確認して `1` に。

## 通信仕様(WebSocket, 既定 ws://<Mac>:8765)
端末→Mac: 先頭1バイトのタグ付きバイナリ。`0x01`=PCM16LE 16kHz mono(任意長) / `0x02`=JPEG。
テキスト `{"t":"hello","pair":["ja","en"],"topic":"..."}` で言語ペア・話題を設定(途中でも可)。

Mac→端末(テキスト JSON):
- `{"t":"face","state":"listening|thinking|sad|happy|neutral"}`
- `{"t":"caption","lang","to","orig","trans","final"}` 字幕(final=false は原文だけ)
- `{"t":"look","ex","ey","size"}` 顔の画面中心からのずれ(-1..1)
- `{"t":"speech","sr":16000,"ms","bytes"}` → バイナリ `0x03`+PCM を連続 → `{"t":"speech_end"}`

## 注意・既知の限界
- 長い読み上げ(約 10 秒以上)の最中に Wi-Fi が切れて再接続することがある(電波が弱い時に多い。約 3 秒で自動復帰)。端末は電波の強い場所に置くこと。
- 顔の描画タスクのスタックは 12KB に拡張済み(2KB だと字幕描画で溢れて再起動を繰り返す)。画面は描画タスクだけが触ること(別タスクから触ると SPI の排他が壊れて落ちる)。読み上げ中に画面を撮らないこと。
- 首は公式 StackChan-BSP(Feetech シリアルサーボ)で動かす。`config.h` の可動域は物にぶつからない範囲に。
- 公式 stack-chan(Moddable/TypeScript)ではなく **Arduino + M5Unified + m5stack-avatar** で書いている
  (自前の薄い制御が目的で、Mac と通信できれば十分なため)。
- マイクとスピーカーは M5Unified では同時に使えないため、再生中はマイクを止める(半二重)。
  ブリッジ側も再生時間+0.8秒は聞かない。割り込み発話は未対応。
- 家の Wi-Fi の揺れで s109 の耳が遅れることがある(Sokutsu 本体と同じ。自動でこのMacの耳へ)。
- カメラは M5Unified の I2C を共有して動作確認済み。読み上げ中は画像を送らないため、その間は追従が止まる。
- Vault 方針: IP・Wi-Fi 情報は `config.h`(git 管理外)にだけ置く。
