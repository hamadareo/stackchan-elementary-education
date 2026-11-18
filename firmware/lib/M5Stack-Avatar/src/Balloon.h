// Copyright (c) Shinya Ishikawa. All rights reserved.
// Licensed under the MIT license. See LICENSE file in the project root for full
// license information.

#ifndef BALLOON_H_
#define BALLOON_H_
#define LGFX_USE_V1
#include <M5Unified.h>
#include "DrawContext.h"
#include "Drawable.h"

#ifndef ARDUINO
#include <string>
typedef std::string String;
#endif  // ARDUINO

const int16_t TEXT_HEIGHT = 8;
const int16_t TEXT_SIZE = 2;
const int16_t MIN_WIDTH = 40;
const int cx = 240;
const int cy = 220;

namespace m5avatar {
// 遊びの表示カード: 見出し / 左右 2 列(名前・大きな字・矢印) / 一言。矢印 0=なし 1=上 2=下 3=左 4=右
struct AvatarBoard {
  char title[48], aWho[48], aBig[32], bWho[48], bBig[32], foot[96];
  uint8_t aArrow, bArrow;
  bool on;
};
extern AvatarBoard avatarBoard;
extern volatile uint32_t avatarBoardSeq;
extern char avatarCaption[512];
extern volatile uint32_t avatarCaptionSeq;
// 字幕を安全に読む(書き込み中なら待ち、読み終えて番号が変わっていたらやり直す)
static inline String readCaption() {
  static char tmp[512];                      // 描画タスク 1 つだけが使う(スタックを節約)
  for (int tries = 0; tries < 20; tries++) {
    uint32_t a = avatarCaptionSeq;
    if (a & 1) { vTaskDelay(1); continue; }
    memcpy(tmp, avatarCaption, sizeof tmp);
    if (a == avatarCaptionSeq) { tmp[sizeof tmp - 1] = 0; return String(tmp); }
  }
  return String("");
}
class Balloon final : public Drawable {
 public:
  // constructor
  Balloon() = default;
  ~Balloon() = default;
  Balloon(const Balloon &other) = default;
  Balloon &operator=(const Balloon &other) = default;
  // 字幕: 画面下に折り返して表示(最大3行。長い文は 2.6 秒ごとにページ送り)。元の吹き出しは 1 行で見切れるため作り直した。
  // 矢印(三角形)。cx,cy は中心、r は大きさ
  static void arrow(M5Canvas *spi, int cx, int cy, int r, int dir, uint16_t col) {
    if (dir == 1) spi->fillTriangle(cx, cy - r, cx - r, cy + r, cx + r, cy + r, col);
    else if (dir == 2) spi->fillTriangle(cx, cy + r, cx - r, cy - r, cx + r, cy - r, col);
    else if (dir == 3) spi->fillTriangle(cx - r, cy, cx + r, cy - r, cx + r, cy + r, col);
    else if (dir == 4) spi->fillTriangle(cx + r, cy, cx - r, cy - r, cx - r, cy + r, col);
  }
  // 遊びの表示カード(字幕の上)。2 列なら左右、a だけなら中央に大きく出す
  static void drawBoard(M5Canvas *spi, uint16_t fg, uint16_t bg) {
    static AvatarBoard b;
    static uint32_t seenSeq = 0xFFFFFFFF;
    uint32_t q = avatarBoardSeq;
    if (!(q & 1) && q != seenSeq) {
      AvatarBoard t = avatarBoard;
      if (q == avatarBoardSeq) { b = t; seenSeq = q; }
    }
    if (!b.on) return;
    const int x0 = 6, y0 = 26, w = 308, h = 128;
    spi->fillRoundRect(x0, y0, w, h, 10, fg);
    spi->fillRoundRect(x0 + 2, y0 + 2, w - 4, h - 4, 9, bg);
    spi->setTextColor(fg, bg);
    spi->setTextDatum(TC_DATUM);
    spi->setFont(&fonts::efontJA_16);
    spi->setTextSize(1);
    if (b.title[0]) spi->drawString(b.title, x0 + w / 2, y0 + 5);
    bool two = b.bBig[0] || b.bArrow || b.bWho[0];
    int colA = two ? x0 + w / 4 : x0 + w / 2, colB = x0 + 3 * w / 4;
    for (int c = 0; c < (two ? 2 : 1); c++) {
      const char *who = c ? b.bWho : b.aWho, *big = c ? b.bBig : b.aBig;
      uint8_t ar = c ? b.bArrow : b.aArrow;
      int cx = c ? colB : colA;
      spi->setFont(&fonts::efontJA_16);
      spi->setTextSize(1);
      if (who[0]) spi->drawString(who, cx, y0 + 28);
      if (ar) {
        arrow(spi, cx, y0 + 74, 26, ar, fg);
      } else if (big[0]) {
        spi->setFont(&fonts::efontJA_24);
        int len = strlen(big) / 3 + 1;                       // 文字数のめやす(日本語は 3 バイト)
        spi->setTextSize(len <= 3 || (two && len <= 4) ? 2 : 1);
        spi->drawString(big, cx, y0 + (spi->fontHeight() > 30 ? 50 : 62));
      }
    }
    if (two) spi->drawFastVLine(x0 + w / 2, y0 + 30, h - 60, fg);
    if (b.foot[0]) {
      spi->setFont(&fonts::efontJA_16);
      spi->setTextSize(1);
      spi->drawString(b.foot, x0 + w / 2, y0 + h - 24);
    }
    spi->setTextDatum(TL_DATUM);
  }
  void draw(M5Canvas *spi, BoundingRect rect,
            DrawContext *drawContext) override {
    drawBoard(spi, drawContext->getColorPalette()->get(COLOR_BALLOON_FOREGROUND), drawContext->getColorPalette()->get(COLOR_BALLOON_BACKGROUND));
    // 折り返しの計算は字幕が変わったときだけ(毎フレームやると重く、通信タスクを圧迫して切断の原因になった)
    static uint32_t cachedSeq = 0xFFFFFFFF;
    static String lines[14];
    static int n = 0;
    const int W = 296, LH = 19, MAXL = 3, MAXN = 14;
    const lgfx::IFont *font = drawContext->getSpeechFont();
    ColorPalette *cp = drawContext->getColorPalette();
    uint16_t fg = cp->get(COLOR_BALLOON_FOREGROUND);
    uint16_t bg = cp->get(COLOR_BALLOON_BACKGROUND);
    spi->setFont(font);
    spi->setTextSize(1);
    spi->setTextDatum(TL_DATUM);
    spi->setTextColor(fg, bg);
    uint32_t seq = avatarCaptionSeq;
    if (!(seq & 1) && seq != cachedSeq) {
      String text = readCaption();
      cachedSeq = seq;
      n = 0;
      String cur;
      for (size_t i = 0; i < text.length() && n < MAXN;) {
        uint8_t c = (uint8_t)text[i];
        int len = c < 0x80 ? 1 : (c < 0xE0 ? 2 : (c < 0xF0 ? 3 : 4));
        String ch = text.substring(i, i + len);
        i += len;
        if (ch == "\n") { lines[n++] = cur; cur = ""; continue; }
        String t = cur + ch;
        if (cur.length() && spi->textWidth(t.c_str()) > W) {
          int sp = cur.lastIndexOf(' ');
          if (sp > 0 && c < 0x80) { lines[n++] = cur.substring(0, sp); cur = cur.substring(sp + 1) + ch; }
          else { lines[n++] = cur; cur = ch; }
        } else {
          cur = t;
        }
      }
      if (cur.length() && n < MAXN) lines[n++] = cur;
    }
    if (n == 0 || avatarCaption[0] == 0) return;
    int pages = (n + MAXL - 1) / MAXL;
    int page = pages > 1 ? (int)((millis() / 2600) % pages) : 0;
    int first = page * MAXL, cnt = (n - first < MAXL) ? (n - first) : MAXL;
    int h = cnt * LH + 10;
    int y0 = (int)rect.getHeight() - 11 - h;       // 見える範囲の下端(顔を 11px 下げているぶん)に合わせる
    spi->fillRoundRect(2, y0, 316, h, 9, fg);
    spi->fillRoundRect(4, y0 + 2, 312, h - 4, 8, bg);
    for (int i = 0; i < cnt; i++) spi->drawString(lines[first + i].c_str(), 12, y0 + 5 + i * LH);
  }
};

}  // namespace m5avatar

#endif  // BALLOON_H_
