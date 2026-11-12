// ずんだもん風の顔(自作の簡易描画。キャラクターの公式画像は使っていない)。
// 別タスク(core0)で 30fps 描画し、メイン処理(マイク・通信)を邪魔しない。
// 口は再生中の音声の大きさ・波形から動かす(setMouth)。表情・まばたき・視線・字幕つき。
#pragma once
#include <M5Unified.h>
#include <math.h>

namespace zface {

enum Emo { NEUTRAL, HAPPY, THINK, SAD, SURPRISE, SLEEPY, LISTEN, GIGGLE };

static LGFX_Sprite *cv = nullptr;
static volatile Emo emo = NEUTRAL;
static volatile uint32_t emoUntil = 0;           // この時刻に NEUTRAL へ戻る(0=戻さない)
static volatile float mouthOpenT = 0, mouthWideT = 0;
static volatile float gazeX = 0, gazeY = 0;       // -1..1
static volatile bool speaking = false;
static char capBuf[384] = "";
static volatile uint32_t capVer = 0, capAt = 0;
static SemaphoreHandle_t capMu = nullptr;

// ---- 外から呼ぶ ----
static inline void setEmotion(Emo e, uint32_t holdMs = 0) { emo = e; emoUntil = holdMs ? millis() + holdMs : 0; }
static inline void setMouth(float open, float wide) { mouthOpenT = open; mouthWideT = wide; }
static inline void setGaze(float x, float y) { gazeX = constrain(x, -1.f, 1.f); gazeY = constrain(y, -1.f, 1.f); }
static inline void setSpeaking(bool s) { speaking = s; if (!s) { mouthOpenT = 0; mouthWideT = 0; } }
static void setCaption(const char *t) {
  if (!capMu) return;
  xSemaphoreTake(capMu, portMAX_DELAY);
  strncpy(capBuf, t ? t : "", sizeof(capBuf) - 1);
  capBuf[sizeof(capBuf) - 1] = 0;
  capVer++; capAt = millis();
  xSemaphoreGive(capMu);
}

// ---- 描画の部品 ----
static uint16_t C(uint8_t r, uint8_t g, uint8_t b) { return cv->color565(r, g, b); }

static void thickArc(int cx, int cy, int r, float a0, float a1, int th, uint16_t col) {
  float prevx = 0, prevy = 0; bool first = true;
  for (int i = 0; i <= 14; i++) {
    float a = (a0 + (a1 - a0) * i / 14.0f) * (float)M_PI / 180.0f;
    float x = cx + r * cosf(a), y = cy + r * sinf(a);
    if (!first) for (int d = -th / 2; d <= th / 2; d++) cv->drawLine(prevx, prevy + d, x, y + d, col);
    prevx = x; prevy = y; first = false;
  }
}

static void thickLine(int x0, int y0, int x1, int y1, int th, uint16_t col) {
  for (int d = -th / 2; d <= th / 2; d++) cv->drawLine(x0, y0 + d, x1, y1 + d, col);
}

static void draw(float t, float eyeOpen, float mo, float mw, Emo e) {
  const uint16_t BG = C(236, 247, 224), HAIR = C(132, 196, 72), HAIR_D = C(88, 150, 44), SKIN = C(255, 237, 222),
                 EYE = C(52, 36, 40), CHEEK = C(255, 172, 172), MOUTH = C(122, 34, 44), TONGUE = C(238, 110, 128),
                 BEAN = C(176, 222, 104), WHITE = C(255, 255, 255);
  cv->fillScreen(BG);

  float bob = 2.0f * sinf(t / 900.0f) + (speaking ? 4.0f * mo : 0);     // 息づかい・話すと弾む
  int ox = (int)(gazeX * 4), oy = (int)bob - 8;
  int cx = 160 + ox;

  // 後ろ髪・耳・えだまめのかざり
  cv->fillEllipse(cx, 96 + oy, 120, 98, HAIR);
  cv->fillTriangle(cx - 112, 70 + oy, cx - 78, 8 + oy, cx - 42, 50 + oy, HAIR);     // 左の毛先
  cv->fillTriangle(cx + 112, 70 + oy, cx + 78, 8 + oy, cx + 42, 50 + oy, HAIR);     // 右の毛先
  cv->fillRoundRect(cx - 42, 2 + oy, 84, 30, 15, HAIR_D);                           // えだまめのさや
  cv->fillRoundRect(cx - 38, 5 + oy, 76, 24, 12, BEAN);
  for (int i = -1; i <= 1; i++) { cv->fillCircle(cx + i * 24, 17 + oy, 9, C(150, 205, 80)); cv->fillCircle(cx + i * 24 - 2, 14 + oy, 3, C(205, 240, 150)); }

  // 顔
  cv->fillEllipse(cx, 124 + oy, 88, 80, SKIN);
  // 前髪
  cv->fillTriangle(cx - 88, 78 + oy, cx - 26, 52 + oy, cx - 52, 112 + oy, HAIR);
  cv->fillTriangle(cx - 40, 52 + oy, cx + 40, 52 + oy, cx, 104 + oy, HAIR);
  cv->fillTriangle(cx + 88, 78 + oy, cx + 26, 52 + oy, cx + 52, 112 + oy, HAIR);
  cv->fillEllipse(cx, 56 + oy, 92, 22, HAIR);
  // 横髪
  cv->fillEllipse(cx - 94, 150 + oy, 15, 46, HAIR);
  cv->fillEllipse(cx + 94, 150 + oy, 15, 46, HAIR);

  // 目
  int ex[2] = {cx - 38, cx + 38}, ey = 130 + oy;
  int gx = (int)(gazeX * 5), gy = (int)(gazeY * 4);
  float er = 1.0f;
  if (e == SURPRISE) er = 1.22f;
  if (e == LISTEN) er = 1.1f;
  if (e == SLEEPY) eyeOpen = min(eyeOpen, 0.35f);
  for (int i = 0; i < 2; i++) {
    int x = ex[i];
    if (e == HAPPY || e == GIGGLE) {                               // ^ ^
      thickArc(x, ey + 10, 15, 205, 335, 4, EYE);
    } else {
      int rw = (int)(16 * er), rh = max(1, (int)(21 * er * eyeOpen));
      if (e == THINK) { gx = 6; gy = -6; }                          // 考え中は右上を見る
      cv->fillEllipse(x, ey, rw, rh, EYE);
      if (rh > 6) {
        cv->fillCircle(x - 5 + gx / 2, ey - 7 + gy / 2, 5, WHITE);
        cv->fillCircle(x + 5 + gx / 2, ey + 6 + gy / 2, 2, WHITE);
        if (e == SAD) cv->fillCircle(x + (i ? 8 : -8), ey + 3, 3, C(190, 225, 255));
      }
    }
  }
  if (e == SAD) { cv->fillEllipse(cx - 46, ey + 30, 4, 8, C(170, 215, 255)); }
  // まゆ
  int by = 98 + oy;
  uint16_t BROW = C(92, 70, 60);
  if (e == SAD) { thickLine(cx - 56, by + 4, cx - 22, by - 6, 3, BROW); thickLine(cx + 22, by - 6, cx + 56, by + 4, 3, BROW); }
  else if (e == SURPRISE) { thickLine(cx - 54, by - 8, cx - 22, by - 8, 3, BROW); thickLine(cx + 22, by - 8, cx + 54, by - 8, 3, BROW); }
  else if (e == THINK) { thickLine(cx - 54, by, cx - 22, by - 3, 3, BROW); thickLine(cx + 22, by - 8, cx + 54, by - 2, 3, BROW); }
  else { thickLine(cx - 54, by, cx - 22, by, 3, BROW); thickLine(cx + 22, by, cx + 54, by, 3, BROW); }

  // ほお
  int chw = (e == HAPPY || e == GIGGLE) ? 15 : 11;
  cv->fillEllipse(cx - 62, 156 + oy, chw, 8, CHEEK);
  cv->fillEllipse(cx + 62, 156 + oy, chw, 8, CHEEK);
  // 鼻
  cv->fillCircle(cx, 150 + oy, 2, C(230, 190, 170));

  // 口
  int my = 170 + oy;
  if (mo > 0.06f) {                                                  // 話している口: 開き具合=音量、幅=波形
    int w = (int)(9 + 7 * (1.0f - mw) + 13 * mw);                    // 母音「お/う」は丸く、「い/え」は横に広く
    int h = (int)(3 + 20 * mo * (1.0f - 0.45f * mw));
    cv->fillEllipse(cx, my + h / 3, w, h, MOUTH);
    if (h > 10) cv->fillEllipse(cx, my + h / 3 + h / 2, w * 2 / 3, h / 3, TONGUE);
  } else if (e == HAPPY || e == GIGGLE) {
    cv->fillEllipse(cx, my + 2, 13, 9, MOUTH); cv->fillRect(cx - 14, my - 8, 28, 8, SKIN);   // にっこり(開き口)
    cv->fillEllipse(cx, my + 7, 8, 4, TONGUE);
  } else if (e == SURPRISE) {
    cv->fillEllipse(cx, my + 4, 6, 9, MOUTH);
  } else if (e == SAD) {
    thickArc(cx, my + 12, 12, 215, 325, 3, MOUTH);                   // への字
  } else if (e == THINK) {
    thickLine(cx - 8, my + 2, cx + 8, my, 3, MOUTH);                 // 一文字
  } else {
    thickArc(cx, my - 6, 12, 40, 140, 3, MOUTH);                     // ほほえみ
  }

  // 記号(考え中の汗・？/ 眠い z)
  if (e == THINK) {
    int dots = 1 + ((int)(t / 350.0f) % 3);
    for (int i = 0; i < dots; i++) cv->fillCircle(cx + 100 + i * 12, 40 + oy, 4, HAIR_D);
    cv->fillEllipse(cx + 84, 78 + oy, 4, 7, C(170, 215, 255));
  }
  if (e == SLEEPY) {
    cv->setFont(&fonts::Font4); cv->setTextColor(HAIR_D);
    cv->setCursor(cx + 86, 40 + oy - (int)(t / 200) % 10); cv->print("z");
    cv->setCursor(cx + 102, 24 + oy - (int)(t / 260) % 10); cv->print("Z");
  }
}

static void drawCaption(float t) {
  if (!capMu) return;
  static char local[384]; static uint32_t ver = 0xFFFFFFFF;
  if (ver != capVer) {
    xSemaphoreTake(capMu, portMAX_DELAY); strcpy(local, capBuf); ver = capVer; xSemaphoreGive(capMu);
  }
  if (!local[0] || millis() - capAt > 20000) return;                 // 20秒で消える
  const uint16_t PANEL = cv->color565(255, 255, 255), EDGE = cv->color565(88, 150, 44), TXT = cv->color565(40, 40, 40);
  cv->fillRoundRect(6, 184, 308, 52, 10, PANEL);
  cv->drawRoundRect(6, 184, 308, 52, 10, EDGE);
  cv->setClipRect(10, 187, 300, 46);
  cv->setFont(&fonts::efontJA_14);
  cv->setTextColor(TXT);
  cv->setTextWrap(true, false);
  int w = cv->textWidth(local);
  int lines = w / 292 + 1;
  int off = 0;
  if (lines > 3) {                                                    // 長い字幕はゆっくり送る
    int over = (lines - 3) * 16;
    off = -(int)(fmodf((millis() - capAt) / 25.0f, over + 60.0f));
    if (off < -over) off = -over;
  }
  cv->setCursor(10, 189 + off);
  cv->print(local);
  cv->clearClipRect();
}

static void task(void *) {
  float eye = 1.0f, mo = 0, mw = 0.4f;
  uint32_t nextBlink = millis() + 2500, blinkT0 = 0;
  for (;;) {
    uint32_t now = millis();
    float t = (float)now;
    if (emoUntil && now > emoUntil) { emo = NEUTRAL; emoUntil = 0; }
    Emo e = emo;
    // まばたき(0.16秒)
    if (!blinkT0 && now > nextBlink) { blinkT0 = now; nextBlink = now + random(2200, 5200); }
    float target = 1.0f;
    if (blinkT0) {
      float p = (now - blinkT0) / 160.0f;
      if (p >= 1.0f) blinkT0 = 0; else target = fabsf(p - 0.5f) * 2.0f * 0.95f + 0.05f;
    }
    eye += (target - eye) * 0.7f;
    // 口: 開くのは速く、閉じるのはなめらかに
    float mt = mouthOpenT, wt = mouthWideT;
    mo += (mt - mo) * (mt > mo ? 0.7f : 0.35f);
    mw += (wt - mw) * 0.4f;
    draw(t, eye, mo, mw, e);
    drawCaption(t);
    cv->pushSprite(&M5.Display, 0, 0);
    vTaskDelay(pdMS_TO_TICKS(28));
  }
}

static void begin() {
  M5.Display.setRotation(1);
  capMu = xSemaphoreCreateMutex();
  cv = new LGFX_Sprite(&M5.Display);
  cv->setPsram(true);
  cv->setColorDepth(16);
  cv->createSprite(320, 240);
  xTaskCreatePinnedToCore(task, "zface", 8192, nullptr, 1, nullptr, 0);
}

}  // namespace zface
