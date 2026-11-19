// StackChan(公式 CoreS3 版) × Sokutsu: 耳・口・顔(StackChan オリジナル)・首・ランプだけの薄い端末。
// 認識・翻訳・読み上げの音づくりは Mac(+s109)のブリッジ(bridge/stackchan_bridge.py)が行う。
//
// 端末→Mac: バイナリ [0x01]+PCM16LE 16kHz mono(マイク)
// Mac→端末: JSON {"t":"face"|"caption"|"look"|"speech"|"speech_end"|"hello_ack"} と [0x03]+PCM16LE(読み上げ)
// 通信路: USB シリアルと Wi-Fi WebSocket の両対応(つながった方を使う)。
#include <Arduino.h>
#include <M5StackChan.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "config.h"
#include "face.h"
#include "camera.h"

static constexpr int SR = 16000;
static constexpr int MIC_CHUNK = 512;                  // サンプル(32ms)
static constexpr size_t SPEECH_MAX = SR * 2 * 30;      // 1区間の読み上げは最大30秒(PSRAM)。2区間ぶん持つ

static WebSocketsClient ws;
static void onWs(WStype_t type, uint8_t *p, size_t n);

// ---- 通信路: USB シリアル と Wi-Fi WebSocket の両対応 ----
// どちらからでも hello_ack をもらえた側を使う(USB を外しても Wi-Fi で続く。自立動作)。
// シリアルのフレーム: A5 5A | 種別(1=テキスト,2=バイナリ) | 長さ u32 LE | 本体
enum Link { LK_NONE, LK_USB, LK_WIFI };
static Link active = LK_NONE, rxSrc = LK_NONE;
static constexpr bool WIFI_ON = sizeof(WIFI_SSID) > 1;
static void serialSend(uint8_t kind, const uint8_t *d, size_t n) {
  uint8_t h[7] = {0xA5, 0x5A, kind, (uint8_t)n, (uint8_t)(n >> 8), (uint8_t)(n >> 16), (uint8_t)(n >> 24)};
  Serial.write(h, 7);
  Serial.write(d, n);
}
static void linkText(const String &s) {
  if (active != LK_WIFI) serialSend(1, (const uint8_t *)s.c_str(), s.length());
  if (WIFI_ON && active != LK_USB && ws.isConnected()) { String t = s; ws.sendTXT(t); }
}
static void linkBin(const uint8_t *d, size_t n) {
  if (active == LK_USB) serialSend(2, d, n);
  else if (active == LK_WIFI && ws.isConnected()) ws.sendBIN((uint8_t *)d, n);
}
static uint32_t lastUsbRx = 0;                           // USB から最後に受け取った時刻(抜かれたかの判断に使う)
static void serialPoll() {
  static uint8_t *rx = nullptr; static size_t need = 0, got = 0; static uint8_t kind = 0, hs = 0, hdr[5];
  static uint32_t lastByte = 0;
  if (!rx) rx = (uint8_t *)ps_malloc(SPEECH_MAX + 16);
  if (hs != 0 && millis() - lastByte > 500) hs = 0;       // 枠の途中で止まったら捨てて同期し直す
  while (Serial.available()) {
    lastByte = millis();
    int c = Serial.read();
    if (hs == 0) { if (c == 0xA5) hs = 1; }
    else if (hs == 1) { hs = (c == 0x5A) ? 2 : 0; }
    else if (hs < 7) {
      hdr[hs - 2] = c; hs++;
      if (hs == 7) {
        kind = hdr[0]; need = hdr[1] | (hdr[2] << 8) | (hdr[3] << 16) | ((size_t)hdr[4] << 24); got = 0;
        if (need == 0 || need > SPEECH_MAX + 8) hs = 0;
      }
    } else {
      rx[got++] = c;
      if (got == need) { hs = 0; rxSrc = LK_USB; lastUsbRx = millis(); onWs(kind == 1 ? WStype_TEXT : WStype_BIN, rx, need); }
    }
  }
}
static void onWsWifi(WStype_t type, uint8_t *p, size_t n) {
  rxSrc = LK_WIFI;
  if (type == WStype_DISCONNECTED && active == LK_WIFI) active = LK_NONE;
  onWs(type, p, n);
}
static void linkPoll() {
  serialPoll();
  if (WIFI_ON) ws.loop();
}

// ---- 状態 ----
static int clkH = -1, clkM = 0, clkS = 0; static uint32_t clkAt = 0;
static String earWhere = "?", llmWhere = "?", llmModel = "";   // llmModel: 使っている翻訳モデルの短い名前(上部バーに出す)
static float battPct = 100; static bool battLow = false, battChg = false;
namespace m5avatar { extern void (*avatarStripOverlay)(lgfx::LovyanGFX *, int, int); }
static void statusBar(bool force);
static char devName[28] = "";
static volatile bool barDirty = true;
static bool shotReq = false;
static void shotPump();
static uint32_t shotAt = 0;
static bool asleep = false;
static uint8_t baseBright = 140;                         // 通常時の画面の明るさ(ブリッジが部屋の明るさで調整)
static uint8_t spkVol = 200;                             // スピーカー音量 0..255(電源を切っても覚える)
static void setVolume(int v, bool show = true) {
  v = constrain(v, 20, 255);
  spkVol = v;
  M5.Speaker.setVolume(spkVol);
  { Preferences p; if (p.begin("sc", false)) { p.putUChar("vol", spkVol); p.end(); } }
  if (show) { char b[24]; snprintf(b, sizeof b, "音量 %d%%", v * 100 / 255); zface::setCaption(b); }
  { JsonDocument d; d["t"] = "vol"; d["v"] = spkVol; String o; serializeJson(d, o); linkText(o); }   // 管理画面へ現在の音量を知らせる
  statusBar(true);
}
static String bridgeHost = BRIDGE_HOST;                // mDNS で見つかれば更新(見つからなければ config.h の値)
static uint16_t bridgePort = BRIDGE_PORT;
enum Mode { LISTEN, RECEIVING, SPEAKING };
static Mode mode = LISTEN;
// 読み上げは文ごとに届く。再生中に次の文を受け取れるよう、2面のバッファを交互に使う
static uint8_t *speechBuf[2] = {nullptr, nullptr};
static size_t speechLenB[2] = {0, 0};
static int recvIdx = 0, playIdx = -1, pendingIdx = -1, recvSeq = 0;
static bool recvActive = false;
static uint32_t lastServoMs = 0, lastLookMs = 0, listenAgainAt = 0, recvSince = 0, lastActivity = 0, smileAt = 0;
static float panDeg = 0, tiltDeg = 250, panTarget = 0, tiltTarget = 250;

// ---- 頭のランプ(RGB LED 12個)。状態を色で見せる ----
// 呼吸のように連続で更新するとマイクに雑音が乗る(I2C)ので、色は状態が変わったときだけ書く
enum Lamp { L_IDLE, L_LISTEN, L_THINK, L_SPEAK, L_ERROR, L_SLEEP };
static Lamp lamp = L_IDLE;
static void setLamp(Lamp l) { lamp = l; }
static void lampStep() {
  static Lamp shown = (Lamp)99;
  if (lamp == shown) return;
  shown = lamp;
  switch (lamp) {
    case L_IDLE:   M5StackChan.showRgbColor(14, 22, 14); break;     // 待機: 弱い緑(ずんだ)
    case L_LISTEN: M5StackChan.showRgbColor(10, 20, 90); break;     // 聞いている: 青
    case L_THINK:  M5StackChan.showRgbColor(90, 62, 0); break;      // 翻訳中: 黄
    case L_SPEAK:  M5StackChan.showRgbColor(10, 100, 20); break;    // 読み上げ中: 緑
    case L_ERROR:  M5StackChan.showRgbColor(120, 0, 0); break;      // 失敗: 赤
    case L_SLEEP:  M5StackChan.showRgbColor(2, 3, 2); break;        // 眠い: ほぼ消灯
  }
}

// ---- 首: 公式 BSP の Motion(SCS0009 シリアルサーボ)。角度は 0.1 度単位 ----
// X(左右) -1280..1280, Y(上下) 0..900。安全のため可動域を絞る(config.h)
struct GStep { uint32_t at; int dp, dt; };               // ジェスチャーの1コマ(開始からの ms, 基準からのずれ)
static GStep gest[10]; static int gestN = 0, gestI = 0; static uint32_t gestT0 = 0;
static int gOffP = 0, gOffT = 0, gestSpeed = 0;
static void gesture(const GStep *s, int n, int speed = 650) {
  gestN = min(n, 10); for (int i = 0; i < gestN; i++) gest[i] = s[i];
  gestI = 0; gestT0 = millis(); gestSpeed = speed;
}
static void nod()      { static const GStep g[] = {{0, 0, -90}, {200, 0, 0}, {400, 0, -90}, {600, 0, 0}}; gesture(g, 4); }
static void tiltHead() { static const GStep g[] = {{0, 110, 30}}; gesture(g, 1, 400); }
static void wiggle()   { static const GStep g[] = {{0, 140, 0}, {180, -140, 0}, {360, 140, 0}, {540, -140, 0}, {720, 0, 0}}; gesture(g, 5, 800); }
static void untilt()   { static const GStep g[] = {{0, 0, 0}}; gesture(g, 1, 400); }

static void servoStep() {
#if SERVO_ENABLE
  static uint32_t last = 0;
  if (gestI < gestN && millis() - gestT0 >= gest[gestI].at) { gOffP = gest[gestI].dp; gOffT = gest[gestI].dt; gestI++; last = 0; }
  if (millis() - last < 60) return;
  last = millis();
  float pt = constrain(panTarget + gOffP, SERVO_PAN_MIN, SERVO_PAN_MAX);
  float tt = constrain(tiltTarget + gOffT, SERVO_TILT_MIN, SERVO_TILT_MAX);
  if (fabsf(pt - panDeg) < 20 && fabsf(tt - tiltDeg) < 20) return;          // 2度未満は動かさない
  panDeg = pt; tiltDeg = tt; lastServoMs = millis();
  M5StackChan.Motion.move((int)panDeg, (int)tiltDeg, gestSpeed ? gestSpeed : SERVO_SPEED);
#endif
}

static void idleWander() {                              // 人が見えない間、ときどき静かにあたりを見る(控えめ)
  static uint32_t next = 0;
  if (millis() < next || mode != LISTEN) return;
  if (millis() - lastLookMs < 12000) return;             // 顔を追っている最中・直後は動かさない
  int r = random(100);
  if (r < 75) {                                          // ゆっくり別の方向を見る
    gestSpeed = random(120, 260);
    panTarget = random((int)(SERVO_PAN_MIN * 0.35f), (int)(SERVO_PAN_MAX * 0.35f));
    tiltTarget = random(220, 330);
    zface::setGaze(panTarget / (float)SERVO_PAN_MAX, (tiltTarget - 300) / 250.0f);
  } else if (r < 90) {                                   // ふしぎそうに首をかしげる
    static const GStep g[] = {{0, 140, 30}, {1400, 0, 0}};
    gesture(g, 2, 200); zface::setEmotion(zface::THINK, 1800);
  }                                                      // 残りは何もしない
  next = millis() + random(12000, 30000);
}

// ---- 顔の状態名(ブリッジからの指示) ----
static void setFace(const String &s) {
  lastActivity = millis();
  if (s == "listening")      { zface::setEmotion(zface::LISTEN, 6000); setLamp(L_LISTEN); }
  else if (s == "thinking")  { zface::setEmotion(zface::THINK, 15000); setLamp(L_THINK); tiltHead(); }
  else if (s == "sad")       { zface::setEmotion(zface::SAD, 3500); setLamp(L_ERROR); untilt(); }
  else if (s == "happy")     { zface::setEmotion(zface::HAPPY, 2500); setLamp(L_IDLE); }
  else if (s == "giggle")    { zface::setEmotion(zface::GIGGLE, 2500); setLamp(L_IDLE); }
  else if (s == "surprise")  { zface::setEmotion(zface::SURPRISE, 2500); setLamp(L_IDLE); }
  else                       { zface::setEmotion(zface::NEUTRAL); setLamp(L_IDLE); untilt(); }
}

// ---- 読み上げ再生(マイクと同時には使えないので入れ替える)。口は音声の波形から動かす ----
static float mouthFromPcm(const int16_t *pcm, size_t n, size_t pos) {
  const size_t W = SR / 50;                            // 20ms
  if (pos + W > n) { zface::setMouth(0, 0.3f); return 0; }
  double e = 0; int zc = 0;
  for (size_t i = 0; i < W; i++) {
    float x = pcm[pos + i]; e += x * x;
    if (i && ((pcm[pos + i - 1] < 0) != (pcm[pos + i] < 0))) zc++;
  }
  float rms = sqrt(e / W);
  float open = constrain(powf(rms / 6500.0f, 0.65f), 0.0f, 1.0f);
  if (rms < 350) open = 0;                             // 息つぎは口を閉じる
  float wide = constrain((zc / (float)W - 0.04f) / 0.2f, 0.0f, 1.0f);   // 波形が細かい=「い・え・し」
  zface::setMouth(open, wide);
  return open;
}

static volatile bool hushReq = false;                  // 管理画面の「中止」: いま鳴っている声と、あとに続く声をすぐ止める

// 声を出している間も、顔を追えるように、カメラ画像を間引いて送る(音の受信中は送らない。音を最優先)
static void camWhileSpeaking() {
  static uint32_t last = 0;
  if (!cam::ready || recvActive || active == LK_NONE) return;
  if (millis() - last < 700) return;
  last = millis();
  linkBin(cam::jpg, cam::jpgLen + 1);
  cam::ready = false;
}

static void playOne(int idx) {
  if (!speechLenB[idx] || hushReq) return;
  const uint8_t *buf = speechBuf[idx];
  size_t len = speechLenB[idx];
  playIdx = idx;
  bool pl = M5.Speaker.playRaw((const int16_t *)buf, len / 2, SR, false, 1, 0, true);
  { JsonDocument d; d["t"] = "dbg"; d["play"] = pl; d["bytes"] = len; String o; serializeJson(d, o); linkText(o); }
  uint32_t t0 = millis();
  size_t nS = len / 2;
  static uint32_t lastBob = 0; static int bobDir = 1;
  gestN = gestI = 0; gestSpeed = 600;
  while (M5.Speaker.isPlaying() && millis() - t0 < 40000 && !hushReq) {
    float open = mouthFromPcm((const int16_t *)buf, nS, (size_t)((millis() - t0) * (SR / 1000)));
    // 話しながら首も動かす: 声の強いところでうなずき、左右にもゆれる(身ぶり)
    if (open > 0.6f && millis() - lastBob > 900) { lastBob = millis(); bobDir = -bobDir; gOffT = -30; gOffP = bobDir * 30; }
    else if (millis() - lastBob > 300) { gOffT = 0; gOffP = bobDir * 10; }
    servoStep();
    zface::keepCaption();
    camWhileSpeaking();
    linkPoll();                                          // 再生中に次の文が届く(別の面に入る)
    delay(15);
  }
  if (hushReq) M5.Speaker.stop();
  gOffT = 0; gOffP = 0;
  playIdx = -1;
}

static void playSpeech(int idx) {
  if (!speechLenB[idx]) { if (!recvActive) mode = LISTEN; return; }
  mode = SPEAKING;
  setLamp(L_SPEAK);
  zface::setEmotion(zface::NEUTRAL);
  zface::setSpeaking(true);
  M5.Mic.end();
  bool spOk = M5.Speaker.begin();
  M5.Speaker.setVolume(spkVol);
  if (!spOk) { JsonDocument d; d["t"] = "dbg"; d["spk_begin"] = false; String o; serializeJson(d, o); linkText(o); }
  playOne(idx);
  uint32_t waitUntil = millis() + 400;                   // 次の文が届き終わるのを少し待つ(文のあいだを詰める)
  while (true) {
    if (hushReq) { pendingIdx = -1; break; }
    if (pendingIdx >= 0) { int n = pendingIdx; pendingIdx = -1; speechLenB[idx] = 0; idx = n; playOne(idx); waitUntil = millis() + 400; continue; }
    if (recvActive || millis() < waitUntil) { linkPoll(); delay(5); continue; }
    break;
  }
  speechLenB[0] = speechLenB[1] = 0;
  hushReq = false;
  zface::setSpeaking(false);
  M5.Speaker.end();
  M5.Mic.begin();
  zface::setEmotion(zface::HAPPY, 2200);               // 言い終えたらにっこり
  setLamp(L_IDLE);
  nod();                                               // うなずく
  lastActivity = millis();
  listenAgainAt = millis() + 700;                      // 残響ぶん待ってから聞く
  mode = LISTEN;
}

// ---- 通信 ----
static void sendHello() {
  JsonDocument d;
  d["t"] = "hello";
  static String devId;                                  // 機器の ID: Wi-Fi の MAC アドレス(複数台を見分ける)
  if (devId.length() == 0) { devId = WiFi.macAddress(); devId.replace(":", ""); devId.toLowerCase(); }
  d["id"] = devId;
  d["fw"] = __DATE__ " " __TIME__;
  d["pair"][0] = PAIR_A;
  d["pair"][1] = PAIR_B;
  String s; serializeJson(d, s);
  linkText(s);
}
static void serialHello() {   // つながるまで2秒ごとに、USB と Wi-Fi の両方へ挨拶(Mac 側が開くまで届かないため)
  static uint32_t t = 0;
  if (active == LK_NONE && millis() - t > 2000) { t = millis(); sendHello(); }
}

static uint32_t discAt = 0, discHeap = 0; static int discMode = -1;
static void onWs(WStype_t type, uint8_t *p, size_t n) {
  switch (type) {
    case WStype_CONNECTED: {
      sendHello();
      JsonDocument dd; dd["t"] = "dbg"; dd["up_s"] = millis() / 1000;           // 再接続の診断(再起動か、通信だけの切断か)
      dd["disc_ago_ms"] = discAt ? (int)(millis() - discAt) : -1; dd["disc_mode"] = discMode;
      dd["min_heap"] = ESP.getMinFreeHeap(); dd["disc_heap"] = discHeap;
      String o; serializeJson(dd, o); linkText(o);
      break; }
    case WStype_DISCONNECTED:
      discAt = millis(); discMode = (int)mode; discHeap = ESP.getFreeHeap();
      zface::setEmotion(zface::SLEEPY);
      break;
    case WStype_TEXT: {
      JsonDocument d;
      if (deserializeJson(d, p, n)) return;
      String t = d["t"] | "";
      if (t == "hello_ack") {
        active = rxSrc;
        lastActivity = millis();
        zface::setEmotion(zface::HAPPY, 2000);
        zface::setCaption("つながりました");
      } else if (t == "face") {
        setFace(d["state"] | "neutral");
      } else if (t == "caption") {
        String orig = d["orig"] | "", trans = d["trans"] | "";
        bool fin = d["final"] | false;
        bool showT = fin && trans.length();
        String lg = showT ? String((const char *)(d["to"] | "ja")) : String((const char *)(d["lang"] | "ja"));
        zface::setCaptionLang(lg.c_str());
        zface::setCaption((showT ? trans : orig).c_str());
        lastActivity = millis();
      } else if (t == "board") {                          // 遊びの表示カード(じゃんけんの手・おみくじの結果など)
        if (d["clear"] | false) {
          zface::clearBoard();
        } else if (d["ls"].is<JsonArray>()) {              // 一覧(言える遊びの名前)
          static char items[6][32];
          const char *ptr[6];
          int n = 0;
          for (JsonVariant v : d["ls"].as<JsonArray>()) {
            if (n >= 6) break;
            strlcpy(items[n], v.as<const char *>() ? v.as<const char *>() : "", sizeof items[n]);
            ptr[n] = items[n];
            n++;
          }
          zface::setBoardList(d["title"] | "", ptr, n, d["foot"] | "", d["ms"] | 8000);
        } else {
          auto arr = [](const char *a) { return !strcmp(a, "up") ? 1 : !strcmp(a, "down") ? 2 : !strcmp(a, "left") ? 3 : !strcmp(a, "right") ? 4 : 0; };
          auto mk = [](const char *a) { return !strcmp(a, "ok") ? 1 : !strcmp(a, "ng") ? 2 : !strcmp(a, "q") ? 3 : !strcmp(a, "star") ? 4 : !strcmp(a, "note") ? 5 : 0; };
          auto ico = [](const char *a) { return !strcmp(a, "gu") ? 1 : !strcmp(a, "choki") ? 2 : !strcmp(a, "pa") ? 3 : 0; };
          zface::setBoard(d["title"] | "", d["aw"] | "", d["ab"] | "", arr(d["aa"] | ""), d["bw"] | "", d["bb"] | "", arr(d["ba"] | ""), d["foot"] | "", d["ms"] | 6000,
                          ico(d["ai"] | ""), ico(d["bi"] | ""), !strcmp(d["fx"] | "", "win") ? 1 : 0,
                          !strcmp(d["st"] | "", "word") ? 1 : !strcmp(d["st"] | "", "omikuji") ? 2 : !strcmp(d["st"] | "", "riddle") ? 3 : !strcmp(d["st"] | "", "mimic") ? 4 : 0,
                          mk(d["am"] | ""), mk(d["bm"] | ""), (int)(d["hl"] | 0), d["cc"] | "");
        }
        lastActivity = millis();
      } else if (t == "look") {                          // 画面中心からのずれ(-1..1)で首と目を向ける
        lastLookMs = millis();
        float ex = d["ex"] | 0.0f, ey = d["ey"] | 0.0f;
        zface::setGaze(ex, ey);       // 目線だけ(首は servo 指示で動かす)
        lastActivity = millis();                                    // 人が見えたら起きる
      } else if (t == "bright") {                        // 部屋の明るさに合わせた画面の明るさ
        baseBright = constrain((int)(d["v"] | 140), 15, 255);
        if (!asleep) M5.Display.setBrightness(baseBright);
      } else if (t == "volume") {                        // 音量: v=絶対(0..255) / delta=相対
        if (!d["v"].isNull()) setVolume(d["v"].as<int>());
        else setVolume((int)spkVol + (d["delta"] | 0));
      } else if (t == "shot") {                          // 画面を撮って送る(デバッグ用)
        shotReq = true;
      } else if (t == "faceStyle") {                     // 顔の種類(まる目・ガーリー・わんこ など)と色
        zface::setStyle(d["id"] | 0);
        barDirty = true;
      } else if (t == "name") {                          // 機器名(画面の右下に出す)
        snprintf(devName, sizeof devName, "%s", (const char *)(d["name"] | ""));
        barDirty = true;
      } else if (t == "status") {                       // 時刻と処理機器(上部バー)
        clkH = d["h"] | 0; clkM = d["m"] | 0; clkS = d["s"] | 0; clkAt = millis();
        earWhere = d["ear"] | "?"; llmWhere = d["llm"] | "?"; llmModel = d["model"] | "";
        statusBar(true);
      } else if (t == "servo") {                         // Mac からの首の相対指示(顔追従)。単位は 0.1 度
        lastLookMs = millis();
        if (!d["pan"].isNull()) panTarget = d["pan"].as<float>();            // 絶対位置(校正用)
        if (!d["tilt"].isNull()) tiltTarget = d["tilt"].as<float>();
        panTarget += (float)(d["dpan"] | 0.0f);
        tiltTarget += (float)(d["dtilt"] | 0.0f);
        panTarget = constrain(panTarget, SERVO_PAN_MIN, SERVO_PAN_MAX);
        tiltTarget = constrain(tiltTarget, SERVO_TILT_MIN, SERVO_TILT_MAX);
        gestSpeed = d["speed"] | SERVO_SPEED;
        zface::setGaze(0, 0);
      } else if (t == "hush") {                          // 中止: 声をすぐ止める(再生中は playOne が抜ける。受信中・待機中は捨てる)
        pendingIdx = -1;
        recvActive = false;
        if (mode == SPEAKING || playIdx >= 0) {
          hushReq = true;
        } else {
          speechLenB[0] = speechLenB[1] = 0;
          if (mode == RECEIVING) mode = LISTEN;
        }
      } else if (t == "speech") {
        recvIdx = (playIdx >= 0) ? 1 - playIdx : 0;      // 再生中なら空いている面へ
        speechLenB[recvIdx] = 0;
        recvSince = millis();
        recvActive = true;
        recvSeq = d["seq"] | 0;
        if (mode != SPEAKING) mode = RECEIVING;
      } else if (t == "speech_end") {
        size_t want = d["bytes"] | 0;
        recvActive = false;
        if (want && speechLenB[recvIdx] != want) {       // 欠けた音声はノイズになるので鳴らさず、量を Mac へ知らせる
          JsonDocument e; e["t"] = "dbg"; e["short"] = true; e["seq"] = recvSeq; e["got"] = speechLenB[recvIdx]; e["want"] = want;
          String o; serializeJson(e, o); linkText(o);
          speechLenB[recvIdx] = 0;
          if (mode == RECEIVING) mode = LISTEN;
          break;
        }
        if (playIdx >= 0 || mode == SPEAKING) pendingIdx = recvIdx;   // 再生中: 今の文のあとに続けて鳴らす
        else playSpeech(recvIdx);
      }
      break;
    }
    case WStype_BIN:
      if (n > 1 && p[0] == 0x03 && recvActive && speechBuf[recvIdx] && speechLenB[recvIdx] + n - 1 <= SPEECH_MAX) {
        memcpy(speechBuf[recvIdx] + speechLenB[recvIdx], p + 1, n - 1);
        speechLenB[recvIdx] += n - 1;
      }
      break;
    default: break;
  }
}

// ---- マイク → Mac ----
static constexpr int NB = 4;                           // 録音バッファの輪(予約中+送信待ち)
static int16_t micBuf[NB][MIC_CHUNK];
static uint32_t micQueued = 0, micSent = 0;            // 予約した数 / 送り終えた数
static uint8_t txBuf[3 + MIC_CHUNK * 2];
// 音声欠けの調査用の統計(10秒ごとに Mac へ送る): 送ったフレーム数 / 録音の予約に失敗した数 / 送信にかかった最長 ms / メインループの最長 ms
static uint32_t stCam = 0, stSent = 0, stRecFail = 0, stSendMax = 0, stLoopMax = 0;
static uint16_t micSeq = 0;

static void camPump() {                               // 撮れた1枚を Mac へ(聞いている間だけ。再生中は送らない)
  if (!cam::ready) return;
  if (mode != LISTEN || active == LK_NONE) { cam::ready = false; return; }
  uint32_t t0 = millis();
  linkBin(cam::jpg, cam::jpgLen + 1);
  cam::ready = false;
  stCam++;
  uint32_t dt = millis() - t0; if (dt > stSendMax) stSendMax = dt;
}

static void micStats() {
  static uint32_t last = 0;
  if (millis() - last < 10000) return;
  last = millis();
  if (active == LK_NONE) return;
  JsonDocument d; d["t"] = "dbg"; d["mic_sent"] = stSent; d["rec_fail"] = stRecFail; d["send_max_ms"] = stSendMax; d["loop_max_ms"] = stLoopMax;
  if (WIFI_ON) d["rssi"] = WiFi.RSSI();
  d["heap"] = ESP.getFreeHeap();
  d["pan"] = (int)panDeg; d["tilt"] = (int)tiltDeg; d["pan_t"] = (int)panTarget; d["tilt_t"] = (int)tiltTarget; d["cam"] = stCam; d["cam_fail"] = (uint32_t)cam::failures;
  String o; serializeJson(d, o); linkText(o);
  stCam = stSent = stRecFail = stSendMax = stLoopMax = 0;
}

static void micPump() {
  if (mode != LISTEN || active == LK_NONE || millis() < listenAgainAt || !M5.Mic.isEnabled()) {
    micQueued = micSent = 0;                           // 再生などで止めた間の予約は捨てる
    return;
  }
  uint32_t inflight = M5.Mic.isRecording();            // 録音が済んだ分(予約数 - まだ録音中の数)を順番に送る
  while (micQueued - micSent > inflight) {
    txBuf[0] = 0x04;                                   // 0x04 = 連番つき音声: [0x04][seq u16 LE][PCM16LE]
    txBuf[1] = micSeq & 0xFF; txBuf[2] = micSeq >> 8; micSeq++;
    memcpy(txBuf + 3, micBuf[micSent % NB], MIC_CHUNK * 2);
    uint32_t t0 = millis();
    linkBin(txBuf, sizeof(txBuf));
    uint32_t dt = millis() - t0; if (dt > stSendMax) stSendMax = dt;
    stSent++;
    micSent++;
  }
  while (M5.Mic.isRecording() < 2 && micQueued - micSent < NB - 1) {   // 予約は常に2つ先まで
    if (!M5.Mic.record(micBuf[micQueued % NB], MIC_CHUNK, SR)) { stRecFail++; break; }
    micQueued++;
  }
}

// ---- 頭のてっぺんをタッチ: くすぐったがる ----
static void onTouch() {
  static const char *lines[] = {"くすぐったいよ！", "ひゃっ！", "なでなでありがとう", "びっくりした"};
  static int k = 0;
  zface::setEmotion(zface::SURPRISE, 500);
  zface::setCaption(lines[k++ % 4]);
  wiggle();
  lastActivity = millis();
  smileAt = millis() + 520;                            // びっくりのあとに笑う(なでなで: うれしい顔)
}

#ifndef TOUCH_REACT
#define TOUCH_REACT 1   // 頭のてっぺんを撫でると反応する(誤検出対策は touchStep にある)
#endif
// 誤検出対策: 感度を下げ、首が動いている間・再生中は無視し、強さ2以上を0.25秒以上続けて触られたときだけ反応する
static void touchStep() {
  static uint32_t since = 0, cool = 0, freeSince = 0; static bool inited = false, armed = false;
  if (!inited) { inited = true; M5StackChan.TouchSensor.setSensitivity(SI12T_Type_Low, SI12T_Sensitivity_Level_1); M5StackChan.TouchSensor.setResponseCycles(6); M5StackChan.TouchSensor.recalibrate(); freeSince = millis(); }
  M5StackChan.TouchSensor.wasPressed();                  // 内部の押下フラグを消費しておく
  auto &in = M5StackChan.TouchSensor.getIntensities();
  uint8_t mx = max(in[0], max(in[1], in[2]));
  bool busy = gestI < gestN || millis() - lastServoMs < 1500 || mode != LISTEN;
  if (mx == 0) { if (!freeSince) freeSince = millis(); if (millis() - freeSince > 600) armed = true; }   // 離れていることを確認
  else freeSince = 0;
  if (busy || mx < 2 || !armed) { since = 0; return; }   // 触りっぱなし(センサー固着)では反応しない
  if (!since) since = millis();
  if (millis() - since >= 250 && millis() > cool) {
    cool = millis() + 3000; since = 0; armed = false;
    JsonDocument d; d["t"] = "event"; d["name"] = "touch"; d["lv"] = mx;
    String o; serializeJson(d, o); linkText(o);
    onTouch();
  }
}

// ---- 上部の状態バー: 時刻 / 処理している機器(耳・訳) / 電池残量 ----
// 画面は顔の描画タスクだけが触る(別タスクから触ると SPI バスの排他が壊れて落ちる)。
// loop 側は表示内容を作るだけ(statusBar)。実際の描画は顔の描画の直後に呼ばれる barDraw() が行う。
static char barTm[8] = "--:--", barEar[8] = "?", barLlm[16] = "?"; static bool barGpu = false;
static volatile bool shotDo = false, shotDone = false;
static uint8_t *shotBuf = nullptr;
static void statusBar(bool force) {
  static uint32_t last = 0; static char prev[96];
  if (!force && millis() - last < 1000) return;
  last = millis();
  char tm[8] = "--:--";
  if (clkH >= 0) {
    uint32_t t = (clkH * 3600UL + clkM * 60UL + clkS) + (millis() - clkAt) / 1000;
    snprintf(tm, sizeof tm, "%02u:%02u", (unsigned)((t / 3600) % 24), (unsigned)((t / 60) % 60));
  }
  bool down = active == LK_NONE;                         // ブリッジとつながっていない
  String ear = down ? String("-") : earWhere, llm = down ? String("切断") : (llmModel.length() ? llmModel : llmWhere);
  char line[96];
  snprintf(line, sizeof line, "%s|%s|%s|%d|%d|%d|%d", tm, ear.c_str(), llm.c_str(), (int)(llmWhere == "s109"), (int)battPct, battChg ? 1 : 0, spkVol);
  if (!force && !strcmp(prev, line)) return;
  strlcpy(prev, line, sizeof prev);
  strlcpy(barTm, tm, sizeof barTm); strlcpy(barEar, ear.c_str(), sizeof barEar); strlcpy(barLlm, llm.c_str(), sizeof barLlm); barGpu = llmWhere == "s109";
  barDirty = true;
}
static void nameOverlay(lgfx::LovyanGFX *g, int x0, int y0) {   // 顔の短冊に、右下の機器名を重ねる
  if (!devName[0]) return;
  const int H = 240, W = 320, top = H - 18;
  if (y0 + 8 <= top || y0 >= H) return;
  g->setFont(&fonts::efontJA_14); g->setTextSize(1); g->setTextDatum(top_left); g->setTextColor(0xFFFF, 0x2104);
  int w = g->textWidth(devName) + 8;
  g->fillRect(W - w - x0, top - y0, w, 18, 0x2104);
  g->drawString(devName, W - w + 4 - x0, top + 2 - y0);
}
static void barDraw() {                                  // 顔の描画タスクから呼ばれる
  auto &d = M5.Display;
  if (shotDo && shotBuf) {                               // スクリーンショット(デバッグ)
    d.clearClipRect();
    d.readRect(0, 0, d.width(), d.height(), (lgfx::swap565_t *)shotBuf);
    d.setClipRect(0, 22, d.width(), d.height() - 22);
    shotDo = false; shotDone = true;
  }
  static uint32_t refresh = 0;
  if (!barDirty && millis() - refresh < 2000) return;    // 顔の描画が上書きしても、2 秒ごとに描き直す
  barDirty = false; refresh = millis();
  d.clearClipRect();
  d.fillRect(0, 0, d.width(), 22, 0x2104);
  d.setTextSize(1); d.setFont(&fonts::efontJA_14); d.setTextColor(0xFFFF, 0x2104); d.setTextDatum(middle_left);
  d.drawString(barTm, 4, 11);
  d.setTextColor(barGpu ? 0x07E0 : (!strcmp(barLlm, "切断") ? 0xF800 : 0xFFE0), 0x2104);
  char mid[40]; snprintf(mid, sizeof mid, "耳%s 訳%s", barEar, barLlm);
  d.drawString(mid, 50, 11);
  d.setTextColor(0xFFFF, 0x2104); d.setTextDatum(middle_center);
  char vol[12]; snprintf(vol, sizeof vol, "音%d", (int)spkVol * 100 / 255);
  d.drawString(vol, 218, 11);
  uint16_t bc = battChg ? 0x07FF : (battPct < 15 ? 0xF800 : (battPct < 35 ? 0xFFE0 : 0x07E0));
  char bt[12]; snprintf(bt, sizeof bt, "%s%d%%", battChg ? "充" : "", (int)battPct);
  d.setTextDatum(middle_right); d.setTextColor(bc, 0x2104);
  d.drawString(bt, d.width() - 4, 11);
  d.setClipRect(0, 22, d.width(), d.height() - 22);
}

static void shotPump() {                             // 画面撮影の依頼と送信(読み上げ中も呼ぶ)
  if (shotReq && !shotDo && mode == LISTEN) {          // 撮影は顔の描画タスクに頼む(読み上げ中はしない: 再生と競合して固まったため)
    if (!shotBuf) shotBuf = (uint8_t *)ps_malloc(M5.Display.width() * M5.Display.height() * 2);
    if (shotBuf) { shotReq = false; shotDone = false; shotDo = true; shotAt = millis(); }
  }
  if (shotDo && millis() - shotAt > 3000) {            // 描画タスクが応じない(診断)
    shotAt = millis(); JsonDocument d; d["t"] = "dbg"; d["shot"] = "no_draw_hook"; String o; serializeJson(d, o); linkText(o);
  }
  if (shotDone) {
    shotDone = false;
    const int W = M5.Display.width(), H = M5.Display.height();
    uint8_t *out = nullptr; size_t len = 0;
    if (fmt2jpg(shotBuf, W * H * 2, W, H, PIXFORMAT_RGB565, 70, &out, &len) && out) {
      uint8_t *msg = (uint8_t *)ps_malloc(len + 1);
      if (msg) { msg[0] = 0x05; memcpy(msg + 1, out, len); linkBin(msg, len + 1); free(msg); }
      free(out);
    } else { JsonDocument d; d["t"] = "dbg"; d["shot"] = "jpg_fail"; String o; serializeJson(d, o); linkText(o); }
  }
}

// ---- 電池(30秒ごとに Mac へ知らせる。少ないとランプと字幕でも知らせる) ----
static void batteryStep() {
  static uint32_t last = 0;
  if (millis() - last < 30000 && last) return;
  last = millis();
  float v = M5StackChan.getBatteryVoltage();
  float cur = M5StackChan.getBatteryCurrent();           // 正=放電、負=充電
  if (v < 2.0f || v > 5.5f) return;                      // 読めていない
  battPct = constrain((v - 3.30f) / (4.15f - 3.30f) * 100.0f, 0.0f, 100.0f);
  bool low = battPct < 15 && cur > 0;
  if (low && !battLow) { zface::setEmotion(zface::SAD, 4000); zface::setCaption("電池が少なくなってきました"); }
  battLow = low; battChg = cur < 0;
  JsonDocument d; d["t"] = "batt"; d["vol"] = spkVol; d["v"] = roundf(v * 100) / 100; d["pct"] = (int)battPct; d["chg"] = cur < 0;
  String o; serializeJson(d, o); linkText(o);
}

// ---- 持ち上げ・ゆすり・倒れ(IMU)。自分のサーボの動きによる揺れは無視する ----
static void imuStep() {
  static uint32_t last = 0, quietUntil = 0, cool = 0;
  if (millis() - last < 40) return;
  last = millis();
  if (gestI < gestN || millis() - lastServoMs < 1200) { quietUntil = millis() + 800; return; }
  if (millis() < quietUntil || millis() < cool) return;
  float ax, ay, az;
  if (!M5.Imu.getAccel(&ax, &ay, &az)) return;
  float g = sqrtf(ax * ax + ay * ay + az * az);
  if (fabsf(g - 1.0f) > 0.55f) {                         // 持ち上げられた・ゆすられた
    cool = millis() + 4000;
    zface::setEmotion(zface::SURPRISE, 1200);
    zface::setCaption("わっ！ゆれてるよ");
    lastActivity = millis();
    JsonDocument d; d["t"] = "event"; d["name"] = "shake"; d["g"] = roundf(g * 100) / 100;
    String o; serializeJson(d, o); linkText(o);
  }
}

// ---- Wi-Fi の状態を知らせる(つながらないときの原因調べ。10秒ごと、つながるまで) ----
static void wifiReport() {
  if (!WIFI_ON) return;
  static uint32_t last = 0; static bool okSent = false;
  if (millis() - last < 10000) return;
  last = millis();
  wl_status_t st = WiFi.status();
  if (st == WL_CONNECTED && okSent) return;
  JsonDocument d; d["t"] = "dbg"; d["wifi"] = (int)st;   // 3=接続 1=SSIDが見つからない 4=接続失敗(パスワード誤り等) 6=切断
  if (st == WL_CONNECTED) { d["ip"] = WiFi.localIP().toString(); d["rssi"] = WiFi.RSSI(); d["ws"] = ws.isConnected(); okSent = true; }
  String o; serializeJson(d, o); linkText(o);
  static int scanState = 0;                               // 0=未 1=実行中 2=済。つながらないとき一度だけ、設定した SSID が見えるか調べる
  if (st != WL_CONNECTED && scanState == 0 && millis() > 25000) { WiFi.scanNetworks(true); scanState = 1; }
}
static void wifiScanPoll() {
  static bool done = false;
  if (done || !WIFI_ON) return;
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING || n == WIFI_SCAN_FAILED) { if (n == WIFI_SCAN_FAILED && millis() > 60000) done = true; return; }
  done = true;
  JsonDocument r; r["t"] = "dbg"; r["scan_total"] = n; r["ssid_seen"] = false;
  for (int i = 0; i < n; i++) if (WiFi.SSID(i) == String(WIFI_SSID)) {
    r["ssid_seen"] = true; r["ch"] = WiFi.channel(i); r["rssi"] = WiFi.RSSI(i); r["auth"] = (int)WiFi.encryptionType(i);   // 3=WPA2 4=WPA/WPA2 5=WEP 7=WPA3
  }
  String q; serializeJson(r, q); linkText(q);
  WiFi.scanDelete();
}

void setup() {
  M5StackChan.begin();                                 // M5.begin + サーボ電源 + ランプ + タッチ
  speechBuf[0] = (uint8_t *)ps_malloc(SPEECH_MAX);
  speechBuf[1] = (uint8_t *)ps_malloc(SPEECH_MAX);
  randomSeed(esp_random());
  { Preferences p; if (p.begin("sc", true)) { spkVol = p.getUChar("vol", 200); p.end(); } }
  zface::begin();
  m5avatar::avatarStripOverlay = nameOverlay;
  m5avatar::avatarPostDraw = barDraw;
  zface::setEmotion(zface::SLEEPY);
  zface::setCaption("起動中…");

#if SERVO_ENABLE
  M5StackChan.Motion.goHome(300);
#endif

#if USE_CAMERA
  if (!cam::begin()) zface::setCaption("カメラが使えません");
#endif

  auto mc = M5.Mic.config();
  mc.sample_rate = SR;
  mc.magnification = 12;                               // 既定16では割れ、32/64では雑音が目立った
  mc.noise_filter_level = 0;                           // 前処理は Mac 側(AutoGain/VAD)に任せる
  M5.Mic.config(mc);
  M5.Speaker.end();                                    // I2S を共有するので、マイクの前にスピーカーを止める
  M5.Mic.begin();
  lastActivity = millis();

  Serial.setRxBufferSize(65536);                       // 既定256Bだと読み上げ音の転送で溢れる
  Serial.setTxBufferSize(16384);
  Serial.setTxTimeoutMs(20);                           // USB を外していても、書き込みで止まらない
  Serial.begin(921600);
  if (WIFI_ON) {
    zface::setCaption("Wi-Fi につなぐ…");
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);                  // つながるのを待たない(USB でもすぐ使える)
    WiFi.setSleep(false);                              // 音声の取りこぼし防止
    ws.begin(bridgeHost.c_str(), bridgePort, "/");
    ws.onEvent(onWsWifi);
    ws.setReconnectInterval(2000);
    ws.enableHeartbeat(15000, 12000, 3);   // 読み上げの終わりにメインが数秒止まっても切らない(15秒ごと・応答待ち12秒・3回失敗で切断)
  } else {
    zface::setCaption("USB で待機中");
  }
}

// ---- ブリッジの自動発見(mDNS)。つながっていないとき、LAN から _sokutsu-bridge を探して接続先を決める ----
// 見つからなければ config.h の BRIDGE_HOST を使う。Mac のアドレスが変わっても設定し直し不要。
static void findBridge() {
  static uint32_t last = 0; static bool mdnsOn = false;
  if (!WIFI_ON || WiFi.status() != WL_CONNECTED || ws.isConnected()) return;
  if (active == LK_USB && millis() - lastUsbRx > 45000) active = LK_NONE;    // USB が抜かれた(15秒ごとの状態通知が途絶えた)
  if (millis() - last < (active == LK_NONE ? 12000UL : 45000UL)) return;     // 探すのは数秒ブロックするので、まばらに(USB 使用中はさらに間隔を空ける)
  last = millis();
  if (!mdnsOn) { mdnsOn = MDNS.begin("stackchan-dev"); if (!mdnsOn) return; }
  int n = MDNS.queryService("sokutsu-bridge", "tcp");
  if (n <= 0) return;
  String ip = MDNS.address(0).toString(); uint16_t port = MDNS.port(0);
  if (ip != bridgeHost || port != bridgePort) {
    bridgeHost = ip; bridgePort = port;
    ws.disconnect();
    ws.begin(bridgeHost.c_str(), bridgePort, "/");
    JsonDocument d; d["t"] = "dbg"; d["bridge_found"] = ip; d["port"] = port;
    String o; serializeJson(d, o); linkText(o);
  }
}

// ---- 無線書き込み(OTA)。Wi-Fi につながったら待ち受ける。書き込み中はマイクとサーボを止める ----
static void otaStep() {
  static bool started = false;
  if (!WIFI_ON) return;
  if (!started && WiFi.status() == WL_CONNECTED) {
    ArduinoOTA.setHostname("stackchan");
    ArduinoOTA.setPassword(OTA_PASS);
    ArduinoOTA.onStart([]() { mode = SPEAKING; M5.Mic.end(); zface::setEmotion(zface::THINK); zface::setCaption("更新中…"); setLamp(L_THINK); lampStep(); });
    ArduinoOTA.onEnd([]() { zface::setCaption("更新完了。再起動します"); });
    ArduinoOTA.begin();
    started = true;
  }
  if (started) ArduinoOTA.handle();
}

void loop() {
  uint32_t loopT0 = millis();
  M5StackChan.update();
  zface::update();
  linkPoll();
  serialHello();
  micPump();
  camPump();

#if TOUCH_REACT
  touchStep();
#endif
  { auto t = M5.Touch.getDetail();                       // 画面上部のバーをタップ: 音量(左半分=下げる / 音の右=上げる)
    static uint32_t tapAt = 0;
    if (t.wasPressed() && t.y < 30 && millis() - tapAt > 300) {
      tapAt = millis();
      if (t.x >= 175 && t.x < 218) setVolume((int)spkVol - 30);        // 「音」の左=下げる
      else if (t.x >= 218 && t.x < 265) setVolume((int)spkVol + 30);   // 右=上げる
    } }
  statusBar(false);
  shotPump();
  if (smileAt && millis() > smileAt) { smileAt = 0; zface::setEmotion(zface::GIGGLE, 1500); }

  if (millis() - lastLookMs > 4000) idleWander();      // 人が見えない間のそぞろ見
  if (recvActive && millis() - recvSince > 15000) { recvActive = false; speechLenB[recvIdx] = 0; if (mode == RECEIVING) mode = LISTEN; }   // 音が最後まで来ない

  // しばらく何も無ければ眠そうにする(音を聞いたら起きる)
  if (!asleep && mode == LISTEN && millis() - lastActivity > 90000) {
    asleep = true; zface::setEmotion(zface::SLEEPY); setLamp(L_SLEEP); M5.Display.setBrightness(30);   // 眠っている間は画面を暗く(電池節約)
  } else if (asleep && millis() - lastActivity < 2000) {
    asleep = false; zface::setEmotion(zface::SURPRISE, 600); setLamp(L_IDLE); M5.Display.setBrightness(baseBright);
  }

  // 長時間ブリッジとつながらないままなら、通信まわりの詰まりを疑って再起動(無人運転の保険。15分)
  { static uint32_t downSince = 0;
    if (active == LK_NONE) { if (!downSince) downSince = millis(); else if (millis() - downSince > 15UL * 60000UL) ESP.restart(); }
    else downSince = 0; }

  servoStep();
  lampStep();
  batteryStep();
  wifiReport();
  if (WiFi.status() != WL_CONNECTED) { static int g = 0; if (g || millis() > 26000) { g = 1; wifiScanPoll(); } }
  imuStep();
  micStats();
  otaStep();
  findBridge();
  uint32_t lt = millis() - loopT0; if (lt > stLoopMax) stLoopMax = lt;
  delay(1);
}
