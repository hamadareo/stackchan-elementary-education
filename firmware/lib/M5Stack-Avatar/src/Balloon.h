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
  uint8_t aIcon, bIcon;                    // 手の絵 0=なし 1=ぐー 2=ちょき 3=ぱー
  uint8_t fx;                              // 演出 0=なし 1=勝ち(きらきら)
  uint8_t style, aMark, bMark, hl;         // 絵の種類 0=なし 1=ことば(しりとり) 2=おみくじ 3=なぞなぞ 4=ものまね / しるし 1=○ 2=× 3=? 4=★ 5=♪ / hl=1 で最後の字を赤く
  char swatch[8];                          // おみくじのラッキーカラー(RRGGBB)
  uint8_t nList;                           // 一覧(遊びの名前)の数。0 なら通常のカード
  char list[6][32];
  bool on;
};
// ずんだもんの チャリはしり(画面の中で動くゲーム)。入力は jump(画面タッチ・頭タッチ・ブリッジ)。結果は evSeq/evKind/evVal で main が読んで、ブリッジへ知らせる
struct AvatarChari {
  volatile bool on, jump, reset;
  volatile uint32_t evSeq, evVal;
  volatile uint8_t evKind;               // 1=ぶつかった(evVal=メートル) 2=100m ごと
};
extern AvatarChari avatarChari;
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
  // 手の絵(じゃんけん)。cx,cy は中心。s は大きさ(1.0 で幅 約48px)。単純な図形だけで描く
  static void hand(M5Canvas *spi, int cx, int cy, int icon, uint16_t fg, uint16_t bg) {
    auto S = [](int v) { return (int)(v * 0.8f); };
    if (icon == 1) {                                           // ぐー: こぶし+指のくぼみ+親指
      spi->fillRoundRect(cx - S(32), cy - S(26), S(64), S(56), S(18), fg);
      for (int i = -1; i <= 1; i++) spi->drawFastVLine(cx + S(i * 16), cy - S(24), S(20), bg);
      spi->fillRoundRect(cx - S(42), cy + S(4), S(28), S(16), S(8), fg);
      spi->drawRoundRect(cx - S(42), cy + S(4), S(28), S(16), S(8), bg);
    } else if (icon == 2) {                                    // ちょき: 立てた 2 本の指+たたんだ指+手のひら
      spi->fillRoundRect(cx - S(16), cy - S(36), S(12), S(44), S(6), fg);
      spi->fillRoundRect(cx + S(4), cy - S(36), S(12), S(44), S(6), fg);
      spi->fillRoundRect(cx - S(26), cy - S(2), S(52), S(34), S(12), fg);
      spi->drawFastVLine(cx - S(10), cy + S(4), S(12), bg);
      spi->drawFastVLine(cx + S(10), cy + S(4), S(12), bg);
    } else if (icon == 3) {                                    // ぱー: 5 本の指+手のひら
      const int xs[4] = {-24, -11, 2, 15}, hs[4] = {36, 44, 44, 36};
      for (int i = 0; i < 4; i++) spi->fillRoundRect(cx + S(xs[i]), cy - S(hs[i]) + S(6), S(10), S(hs[i]), S(5), fg);
      spi->fillRoundRect(cx - S(28), cy + S(2), S(56), S(32), S(12), fg);
      spi->fillRoundRect(cx - S(40), cy + S(8), S(20), S(11), S(5), fg);
    }
  }
  // 勝ちの演出: 枠のまわりのきらきら(時間で位置が変わる)
  static void sparkle(M5Canvas *spi, int x0, int y0, int w, int h, uint16_t fg) {
    uint32_t t = millis() / 220;
    for (int i = 0; i < 10; i++) {
      uint32_t r = (t * 2654435761u) ^ (i * 40503u + 977u);
      r ^= r >> 13; r *= 1274126177u; r ^= r >> 16;
      int x = x0 + 4 + (int)(r % (uint32_t)(w - 8)), y = y0 + 4 + (int)((r >> 9) % (uint32_t)(h - 8));
      int z = 2 + (int)((r >> 20) % 3);
      spi->drawFastHLine(x - z, y, z * 2 + 1, fg);
      spi->drawFastVLine(x, y - z, z * 2 + 1, fg);
    }
  }

  // ---- 吹き出し表示(じゃんけんの手・あっちむいてホイの向き): 文字ではなく、色つきのイラストで見せる ----
  // 太さのある線(両端が丸い。指の形に使う)
  static void cap(M5Canvas *spi, int x0, int y0, int x1, int y1, int r, uint16_t col) {
    float dx = (float)(x1 - x0), dy = (float)(y1 - y0);
    float L = sqrtf(dx * dx + dy * dy);
    if (L < 1.0f) { spi->fillCircle(x0, y0, r, col); return; }
    int nx = (int)lroundf(-dy / L * r), ny = (int)lroundf(dx / L * r);
    spi->fillTriangle(x0 + nx, y0 + ny, x0 - nx, y0 - ny, x1 + nx, y1 + ny, col);
    spi->fillTriangle(x0 - nx, y0 - ny, x1 - nx, y1 - ny, x1 + nx, y1 + ny, col);
    spi->fillCircle(x0, y0, r, col);
    spi->fillCircle(x1, y1, r, col);
  }
  // 手のイラスト(ふち取りと肌色)。cx,cy は中心、s は大きさ(1.0 で高さ 約 100px)。icon 1=ぐー 2=ちょき 3=ぱー
  static void handArt(M5Canvas *spi, int cx, int cy, int icon, float s) {
    const uint16_t INK = spi->color565(40, 40, 52), SKIN = spi->color565(255, 214, 168), SHADE = spi->color565(226, 170, 120);
    auto P = [&](float v) { return (int)lroundf(v * s); };
    auto rr = [&](float x, float y, float w, float h, float r, int g, uint16_t c) {
      spi->fillRoundRect(cx + P(x) - g, cy + P(y) - g, P(w) + 2 * g, P(h) + 2 * g, P(r) + g, c);
    };
    auto fg = [&](float x0, float y0, float x1, float y1, float r, int g, uint16_t c) {
      cap(spi, cx + P(x0), cy + P(y0), cx + P(x1), cy + P(y1), P(r) + g, c);
    };
    auto line = [&](float x0, float y0, float x1, float y1, uint16_t c) { spi->drawLine(cx + P(x0), cy + P(y0), cx + P(x1), cy + P(y1), c); };
    auto shape = [&](int g, uint16_t c) {
      if (icon == 1) {                                         // ぐー
        rr(-35, -16, 70, 54, 20, g, c);
        for (int i = 0; i < 4; i++) rr(-34 + i * 17, -24, 17, 32, 8, g, c);
        fg(-27, 20, 14, 20, 9, g, c);
      } else if (icon == 2) {                                  // ちょき
        rr(-28, 6, 56, 42, 16, g, c);
        fg(-11, 12, -20, -38, 8, g, c);
        fg(11, 12, 20, -38, 8, g, c);
        fg(-28, 30, 2, 28, 8, g, c);
      } else if (icon == 3) {                                  // ぱー
        rr(-30, 8, 60, 44, 18, g, c);
        fg(-23, 14, -28, -32, 7.5f, g, c);
        fg(-8, 12, -10, -43, 7.5f, g, c);
        fg(8, 12, 10, -43, 7.5f, g, c);
        fg(23, 14, 28, -32, 7.5f, g, c);
        fg(-29, 36, -47, 12, 8, g, c);
      }
    };
    if (icon < 1 || icon > 3) return;
    shape(3, INK);
    shape(0, SKIN);
    if (icon == 1) {
      for (int i = 1; i < 4; i++) line(-34 + i * 17, -18, -34 + i * 17, -2, SHADE);
      line(-26, 11, 12, 11, SHADE);
    } else if (icon == 2) {
      line(14, 16, 14, 30, SHADE);
      line(22, 16, 22, 30, SHADE);
      line(-24, 22, 4, 20, SHADE);
    } else {
      for (int i = -1; i <= 1; i++) line(i * 16, -14, i * 16, 12, SHADE);
    }
  }
  // 大きな矢印(あっちむいてホイ)。dir 1=上 2=下 3=左 4=右
  static void bigArrow(M5Canvas *spi, int cx, int cy, int dir, float s, uint16_t col) {
    const uint16_t INK = spi->color565(40, 40, 52);
    auto map = [&](int x, int y, int &ox, int &oy) {           // 上向きの座標を、向きに合わせて回す
      int X = (int)lroundf(x * s), Y = (int)lroundf(y * s);
      if (dir == 1) { ox = X; oy = Y; } else if (dir == 2) { ox = X; oy = -Y; } else if (dir == 3) { ox = Y; oy = X; } else { ox = -Y; oy = X; }
    };
    auto poly = [&](int g, uint16_t c) {
      int a[8][2] = {{-12 - g, -2}, {12 + g, -2}, {12 + g, 36 + g}, {-12 - g, 36 + g}, {-32 - g, -4}, {32 + g, -4}, {0, -44 - g}, {0, 0}};
      int px[8], py[8];
      for (int i = 0; i < 8; i++) map(a[i][0], a[i][1], px[i], py[i]);
      spi->fillTriangle(cx + px[0], cy + py[0], cx + px[1], cy + py[1], cx + px[2], cy + py[2], c);
      spi->fillTriangle(cx + px[0], cy + py[0], cx + px[2], cy + py[2], cx + px[3], cy + py[3], c);
      spi->fillTriangle(cx + px[4], cy + py[4], cx + px[5], cy + py[5], cx + px[6], cy + py[6], c);
    };
    for (int dx = -3; dx <= 3; dx += 3) for (int dy = -3; dy <= 3; dy += 3) {      // ふち取り: 少しずらして重ねる
      if (!dx && !dy) continue;
      int sx = cx, sy = cy;
      cx = sx + dx; cy = sy + dy;
      poly(0, INK);
      cx = sx; cy = sy;
    }
    poly(0, col);
  }
  // 吹き出し本体。(tx,ty) はしっぽの先
  static void bubble(M5Canvas *spi, int x, int y, int w, int h, int tx, int ty, uint16_t edge) {
    const uint16_t WHITE = 0xFFFF;
    int bx = tx < x + w / 2 ? x + 26 : x + w - 26;             // しっぽの根もと(先の側へよせる)
    spi->fillTriangle(bx - 15, y + h - 3, bx + 15, y + h - 3, tx, ty, edge);
    spi->fillRoundRect(x, y, w, h, 18, edge);
    spi->fillRoundRect(x + 4, y + 4, w - 8, h - 8, 15, WHITE);
    spi->fillTriangle(bx - 10, y + h - 7, bx + 10, y + h - 7, tx, ty - 7, WHITE);
  }
  static void drawBubbles(M5Canvas *spi, const AvatarBoard &b) {
    const uint16_t GOLD = spi->color565(255, 200, 40), INK = spi->color565(40, 40, 52);
    const uint16_t ZUN = spi->color565(86, 176, 64), SKY = spi->color565(64, 144, 224), RED = spi->color565(238, 82, 60), BLU = spi->color565(40, 110, 220);
    bool hasA = b.aIcon || b.aArrow, hasB = b.bIcon || b.bArrow;
    // 結果のひとこと(短いときだけ。長い案内は字幕に任せる)
    spi->setFont(&fonts::efontJA_16);
    spi->setTextSize(1);
    if (b.foot[0] && spi->textWidth(b.foot) <= 270) {
      int tw = spi->textWidth(b.foot) + 28;
      spi->fillRoundRect(160 - tw / 2, 24, tw, 24, 12, INK);
      spi->fillRoundRect(160 - tw / 2 + 2, 26, tw - 4, 20, 10, GOLD);
      spi->setTextColor(INK, GOLD);
      spi->setTextDatum(MC_DATUM);
      spi->drawString(b.foot, 160, 36);
    }
    spi->setTextDatum(TL_DATUM);
    const int top = 54, hh = 98;
    auto side = [&](int x, int w, uint8_t icon, uint8_t arrow, int tx, uint16_t edge, uint16_t acol, float s, int dy) {
      bubble(spi, x, top, w, hh, tx, top + hh + 12, edge);
      if (icon) handArt(spi, x + w / 2, top + hh / 2 + dy, icon, s);
      else bigArrow(spi, x + w / 2, top + hh / 2 - 2 + dy, arrow, s * 0.9f, acol);
    };
    if (hasA && hasB) {
      side(8, 170, b.aIcon, b.aArrow, 138, ZUN, RED, 0.8f, 0);        // ずんだもん(左・大きく)
      side(188, 124, b.bIcon, b.bArrow, 290, SKY, BLU, 0.6f, 9);      // きみ(右)
      spi->setFont(&fonts::efontJA_16);
      spi->setTextColor(INK, 0xFFFF);
      spi->setTextDatum(TC_DATUM);
      spi->drawString("きみ", 250, top + 7);
      spi->setTextDatum(TL_DATUM);
    } else if (hasA) {
      side(70, 180, b.aIcon, b.aArrow, 138, ZUN, RED, 0.88f, 0);
    } else if (hasB) {
      side(70, 180, b.bIcon, b.bArrow, 200, SKY, BLU, 0.92f, 0);
    }
    if (b.fx == 1) {                                             // 勝ちのきらきら(金色)
      uint32_t t = millis() / 220;
      for (int i = 0; i < 12; i++) {
        uint32_t r = (t * 2654435761u) ^ (i * 40503u + 977u);
        r ^= r >> 13; r *= 1274126177u; r ^= r >> 16;
        int x = 8 + (int)(r % 304u), y = 26 + (int)((r >> 9) % 130u), z = 3 + (int)((r >> 20) % 4);
        spi->fillTriangle(x, y - z * 2, x - z, y, x + z, y, GOLD);
        spi->fillTriangle(x, y + z * 2, x - z, y, x + z, y, GOLD);
        spi->fillTriangle(x - z * 2, y, x, y - z, x, y + z, GOLD);
        spi->fillTriangle(x + z * 2, y, x, y - z, x, y + z, GOLD);
      }
    }
  }

  // ---- ことば・しるしの吹き出し(しりとり・なぞなぞ・おみくじ・ものまね) ----
  static void mark(M5Canvas *spi, int cx, int cy, int kind, float s) {
    const uint16_t INK = spi->color565(40, 40, 52), RED = spi->color565(238, 82, 60), BLU = spi->color565(40, 110, 220), GOLD = spi->color565(255, 200, 40), ZUN = spi->color565(86, 176, 64);
    int r = (int)lroundf(26 * s);
    if (kind == 1) {                                           // ○ せいかい
      spi->fillCircle(cx, cy, r, RED); spi->fillCircle(cx, cy, r - (int)lroundf(9 * s), 0xFFFF);
    } else if (kind == 2) {                                    // × ざんねん
      cap(spi, cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4, (int)lroundf(6 * s), BLU);
      cap(spi, cx + r - 4, cy - r + 4, cx - r + 4, cy + r - 4, (int)lroundf(6 * s), BLU);
    } else if (kind == 3) {                                    // ? なぞなぞ
      spi->fillCircle(cx, cy, r, ZUN);
      spi->setFont(&fonts::efontJA_24); spi->setTextSize(s > 0.8f ? 2 : 1);
      spi->setTextColor(0xFFFF, ZUN); spi->setTextDatum(MC_DATUM);
      spi->drawString("?", cx, cy + 1);
    } else if (kind == 4) {                                    // ★ 星(五芒星を 2 枚の三角で)
      float R = r * 1.1f;
      for (int k = 0; k < 2; k++) {
        float a0 = (k ? 90.0f : -90.0f) * 0.0174533f;
        int px[3], py[3];
        for (int i = 0; i < 3; i++) { float a = a0 + i * 2.0944f; px[i] = cx + (int)lroundf(R * cosf(a)); py[i] = cy + (int)lroundf(R * sinf(a)); }
        spi->fillTriangle(px[0], py[0], px[1], py[1], px[2], py[2], GOLD);
      }
      spi->fillCircle(cx, cy, r / 2, GOLD);
    } else if (kind == 5) {                                    // ♪ ものまね
      spi->fillCircle(cx - r / 3, cy + r / 2, r / 2, INK);
      spi->fillRect(cx - r / 3 + r / 2 - 3, cy - r, 5, r * 3 / 2 + 2, INK);
      spi->fillTriangle(cx + r / 6 + 2, cy - r, cx + r / 6 + 2, cy - r / 4, cx + r, cy - r / 8, INK);
    }
  }
  // 吹き出しの中に、しるし+ことばを入れる。hl=1 で最後の字を赤で大きく
  static void content(M5Canvas *spi, int x, int y, int w, int h, int mk, const char *text, bool hl) {
    const uint16_t INK = spi->color565(40, 40, 52), RED = spi->color565(238, 82, 60);
    bool has = text && text[0];
    if (mk && !has) { mark(spi, x + w / 2, y + h / 2, mk, 1.5f); return; }
    int tx0 = x + 10, tw = w - 20;
    if (mk) { mark(spi, x + 34, y + h / 2, mk, 0.9f); tx0 = x + 66; tw = w - 76; }
    if (!has) return;
    spi->setTextColor(INK, 0xFFFF);
    spi->setTextDatum(ML_DATUM);
    spi->setFont(&fonts::efontJA_24); spi->setTextSize(1);
    if (spi->textWidth(text) > tw) { spi->setFont(&fonts::efontJA_16); }
    if (spi->textWidth(text) <= tw) {                          // 1 行
      int cy = y + h / 2, W = spi->textWidth(text), x1 = mk ? tx0 : tx0 + (tw - W) / 2;
      if (hl) {                                                // 最後の字だけ赤
        int n = strlen(text), k = n;
        do { k--; } while (k > 0 && ((uint8_t)text[k] & 0xC0) == 0x80);
        String head = String(text).substring(0, k);
        spi->drawString(head.c_str(), x1, cy);
        spi->setTextColor(RED, 0xFFFF);
        spi->drawString(text + k, x1 + spi->textWidth(head.c_str()), cy);
      } else spi->drawString(text, x1, cy);
      return;
    }
    String all(text), a, b;                                    // 2 行に折る(1 行目に入る分だけ)
    int i = 0;
    while (i < (int)all.length()) {
      uint8_t c = (uint8_t)all[i]; int len = c < 0x80 ? 1 : (c < 0xE0 ? 2 : (c < 0xF0 ? 3 : 4));
      String t = a + all.substring(i, i + len);
      if (spi->textWidth(t.c_str()) > tw) break;
      a = t; i += len;
    }
    b = all.substring(i);
    while (spi->textWidth(b.c_str()) > tw && b.length() > 3) b = b.substring(0, b.length() - 3);
    spi->drawString(a.c_str(), tx0, y + h / 2 - 11);
    spi->drawString(b.c_str(), tx0, y + h / 2 + 11);
  }
  static uint16_t hex565(M5Canvas *spi, const char *h) {
    if (!h || strlen(h) < 6) return 0xFFFF;
    auto v = [](char c) { return c >= '0' && c <= '9' ? c - '0' : (c | 32) - 'a' + 10; };
    return spi->color565(v(h[0]) * 16 + v(h[1]), v(h[2]) * 16 + v(h[3]), v(h[4]) * 16 + v(h[5]));
  }
  static void drawStyled(M5Canvas *spi, const AvatarBoard &b) {
    const uint16_t GOLD = spi->color565(255, 200, 40), INK = spi->color565(40, 40, 52);
    const uint16_t ZUN = spi->color565(86, 176, 64), SKY = spi->color565(64, 144, 224), RED = spi->color565(238, 82, 60);
    spi->setFont(&fonts::efontJA_16); spi->setTextSize(1);
    if (b.foot[0] && spi->textWidth(b.foot) <= 270 && b.style != 2) {        // 一言のリボン(おみくじは札の中に出す)
      int tw = spi->textWidth(b.foot) + 28;
      spi->fillRoundRect(160 - tw / 2, 24, tw, 24, 12, INK);
      spi->fillRoundRect(160 - tw / 2 + 2, 26, tw - 4, 20, 10, GOLD);
      spi->setTextColor(INK, GOLD); spi->setTextDatum(MC_DATUM);
      spi->drawString(b.foot, 160, 36);
    }
    spi->setTextDatum(TL_DATUM);
    const int top = 54, hh = 98;
    if (b.style == 2) {                                        // おみくじ: 札(ふち赤)+大きな「大吉」の印+ラッキーカラーの見本
      const int x = 40, y = 28, w = 240, h = 124;
      spi->fillRoundRect(x, y, w, h, 14, RED);
      spi->fillRoundRect(x + 4, y + 4, w - 8, h - 8, 11, spi->color565(255, 250, 236));
      spi->setTextColor(RED, spi->color565(255, 250, 236)); spi->setTextDatum(TC_DATUM);
      spi->setFont(&fonts::efontJA_16); spi->drawString("きょうの うんせい", x + w / 2, y + 8);
      spi->setFont(&fonts::efontJA_24); spi->setTextSize(2);
      spi->setTextColor(INK, spi->color565(255, 250, 236));
      spi->drawString(b.aBig, x + w / 2, y + 30);
      spi->setTextSize(1);
      if (b.swatch[0]) { spi->fillCircle(x + 26, y + h - 22, 11, INK); spi->fillCircle(x + 26, y + h - 22, 9, hex565(spi, b.swatch)); }
      spi->setFont(&fonts::efontJA_16); spi->setTextDatum(ML_DATUM); spi->setTextColor(INK, spi->color565(255, 250, 236));
      spi->drawString(b.foot, x + 44, y + h - 22);
      if (b.aMark == 4) { mark(spi, x + 20, y + 28, 4, 0.5f); mark(spi, x + w - 20, y + 28, 4, 0.5f); }
      spi->setTextDatum(TL_DATUM);
    } else {
      bool hasA = b.aBig[0] || b.aMark, hasB = b.bBig[0] || b.bMark;
      auto side = [&](int x, int w, uint16_t edge, int tx, int mk, const char *t, bool hl) {
        bubble(spi, x, top, w, hh, tx, top + hh + 12, edge);
        content(spi, x, top, w, hh, mk, t, hl);
      };
      bool hl = b.hl != 0;
      if (hasA && hasB) {
        side(6, 152, ZUN, 120, b.aMark, b.aBig, hl);
        side(162, 152, SKY, 280, b.bMark, b.bBig, hl && b.style == 1);
        spi->setFont(&fonts::efontJA_16); spi->setTextColor(INK, 0xFFFF); spi->setTextDatum(TC_DATUM);
        spi->drawString("きみ", 238, top + 6); spi->setTextDatum(TL_DATUM);
      } else if (hasA) side(40, 240, ZUN, 138, b.aMark, b.aBig, hl);
      else if (hasB) side(40, 240, SKY, 200, b.bMark, b.bBig, hl);
    }
    if (b.fx == 1) {
      uint32_t t = millis() / 220;
      for (int i = 0; i < 12; i++) {
        uint32_t r = (t * 2654435761u) ^ (i * 40503u + 977u);
        r ^= r >> 13; r *= 1274126177u; r ^= r >> 16;
        int x = 8 + (int)(r % 304u), y = 26 + (int)((r >> 9) % 130u), z = 3 + (int)((r >> 20) % 4);
        spi->fillTriangle(x, y - z * 2, x - z, y, x + z, y, GOLD); spi->fillTriangle(x, y + z * 2, x - z, y, x + z, y, GOLD);
        spi->fillTriangle(x - z * 2, y, x, y - z, x, y + z, GOLD); spi->fillTriangle(x + z * 2, y, x, y - z, x, y + z, GOLD);
      }
    }
  }

  // ---- ずんだもんの チャリはしり ----
  static void chariDraw(M5Canvas *spi) {
    const int GY = 196;                                          // 地面の高さ
    struct Ob { float x; uint8_t type; int16_t w, h; bool on; };
    static Ob obs[5];
    static float dist = 0, speed = 140, yo = 0, vy = 0, nextGap = 0, ang = 0, cloud = 0, hills = 0, stripe = 0, overT = 0, readyT = 0;
    static uint32_t last = 0, seed = 12345, rec = 0;
    static uint8_t phase = 0;                                   // 0=よーい 1=はしる 2=ぶつかった
    static uint32_t mileDone = 0;
    auto rnd = [&]() { seed = seed * 1664525u + 1013904223u; return (seed >> 8) & 0xFFFF; };
    uint32_t now = millis();
    float dt = last ? (now - last) / 1000.0f : 0.016f;
    last = now;
    if (dt > 0.05f) dt = 0.05f;
    if (avatarChari.reset) {
      avatarChari.reset = false; avatarChari.jump = false;
      for (auto &o : obs) o.on = false;
      dist = 0; speed = 140; yo = 0; vy = 0; nextGap = 260; phase = 0; readyT = 0; mileDone = 0; seed ^= now;
    }
    bool jmp = avatarChari.jump;
    avatarChari.jump = false;
    const uint16_t SKY = spi->color565(150, 214, 255), SKY2 = spi->color565(196, 234, 255), GRASS = spi->color565(96, 190, 80), DIRT = spi->color565(150, 110, 70);
    const uint16_t INK = spi->color565(40, 40, 52), ZUN = spi->color565(86, 176, 64), SKIN = spi->color565(255, 224, 190), HAIR = spi->color565(150, 224, 100);
    // ---- 進める
    if (phase == 0) { readyT += dt; if (readyT > 1.3f) phase = 1; }
    if (phase == 1) {
      speed = fminf(290.0f, speed + 6.0f * dt);
      dist += speed * dt;
      cloud += speed * 0.1f * dt; hills += speed * 0.3f * dt; stripe += speed * dt; ang += speed * dt / 13.0f;
      if (jmp && yo <= 0.5f) { vy = 430.0f; }
      if (yo > 0 || vy > 0) { yo += vy * dt; vy -= 1400.0f * dt; if (yo <= 0) { yo = 0; vy = 0; } }
      for (auto &o : obs) if (o.on) { o.x -= speed * dt; if (o.x < -50) o.on = false; }
      nextGap -= speed * dt;
      if (nextGap <= 0) {
        for (auto &o : obs) if (!o.on) {
          uint8_t t = rnd() % 3; o.type = t; o.on = true; o.x = 335;
          o.w = t == 0 ? 30 : (t == 1 ? 22 : 28); o.h = t == 0 ? 22 : (t == 1 ? 34 : 28);
          break;
        }
        nextGap = speed * (0.95f + (rnd() % 100) / 100.0f * 0.9f) + 80;
      }
      uint32_t m = (uint32_t)(dist / 12.0f);
      if (m / 100 > mileDone && m >= 100) { mileDone = m / 100; avatarChari.evKind = 2; avatarChari.evVal = m; avatarChari.evSeq++; }
      for (auto &o : obs) if (o.on) {                            // ぶつかったか(少しゆるめ)
        float px0 = 62, px1 = 88, py0 = GY - yo - 46, py1 = GY - yo;
        if (px1 - 3 > o.x + 3 && px0 + 3 < o.x + o.w - 3 && py1 - 3 > GY - o.h + 3 && py0 < GY) {
          phase = 2; overT = 0; rec = m;
          avatarChari.evKind = 1; avatarChari.evVal = m; avatarChari.evSeq++;
        }
      }
    } else if (phase == 2) {
      overT += dt;
      if (jmp && overT > 0.8f) { avatarChari.reset = true; }
    }
    // ---- 描く: 空・雲・山・地面
    spi->fillRect(0, 0, 320, 120, SKY);
    spi->fillRect(0, 120, 320, GY - 120, SKY2);
    for (int i = 0; i < 3; i++) {
      int cx = 330 - (int)fmodf(cloud + i * 130.0f, 450.0f), cy = 40 + i * 22;
      spi->fillCircle(cx, cy, 13, 0xFFFF); spi->fillCircle(cx + 14, cy + 3, 11, 0xFFFF); spi->fillCircle(cx - 14, cy + 3, 10, 0xFFFF);
    }
    const uint16_t HILL = spi->color565(150, 205, 150);
    for (int i = 0; i < 4; i++) {
      int hx = 330 - (int)fmodf(hills + i * 120.0f, 480.0f);
      spi->fillTriangle(hx - 70, GY, hx, GY - 56 - (i % 2) * 16, hx + 70, GY, HILL);
    }
    spi->fillRect(0, GY, 320, 240 - GY, DIRT);
    spi->fillRect(0, GY, 320, 7, GRASS);
    for (int i = 0; i < 8; i++) { int sx = 330 - (int)fmodf(stripe + i * 48.0f, 384.0f); spi->fillRect(sx, GY + 22, 22, 4, spi->color565(122, 86, 52)); }
    // ---- 障害物
    for (auto &o : obs) if (o.on) {
      int x = (int)o.x, y = GY - o.h;
      if (o.type == 0) { spi->fillRoundRect(x, y, o.w, o.h, 10, INK); spi->fillRoundRect(x + 2, y + 2, o.w - 4, o.h - 3, 8, spi->color565(150, 150, 160)); spi->fillRoundRect(x + 6, y + 5, 9, 5, 2, spi->color565(200, 200, 210)); }
      else if (o.type == 1) { spi->fillTriangle(x - 2, GY, x + o.w / 2, y - 2, x + o.w + 2, GY, INK); spi->fillTriangle(x + 1, GY - 1, x + o.w / 2, y + 2, x + o.w - 1, GY - 1, spi->color565(255, 140, 40)); spi->fillRect(x + 6, y + 14, o.w - 12, 5, 0xFFFF); }
      else { spi->fillRect(x - 1, y - 1, o.w + 2, o.h + 2, INK); spi->fillRect(x + 1, y + 1, o.w - 2, o.h - 2, spi->color565(196, 142, 84)); spi->drawLine(x + 2, y + 2, x + o.w - 3, y + o.h - 3, INK); spi->drawLine(x + o.w - 3, y + 2, x + 2, y + o.h - 3, INK); }
    }
    // ---- ずんだもん(自転車)
    float by = GY - 13 - yo;                                      // 車輪の中心
    float tilt = phase == 2 ? 0.0f : fminf(6.0f, yo * 0.08f);
    int bx = 70;
    if (phase == 2 && overT < 0.5f) { by -= sinf(overT * 12.0f) * 6.0f; }
    for (int w = 0; w < 2; w++) {                                 // 車輪
      int wx = bx - 18 + w * 36;
      spi->fillCircle(wx, (int)by, 13, INK); spi->fillCircle(wx, (int)by, 10, SKY2);
      for (int k = 0; k < 3; k++) { float a = ang + k * 1.0472f; spi->drawLine(wx - (int)(10 * cosf(a)), (int)by - (int)(10 * sinf(a)), wx + (int)(10 * cosf(a)), (int)by + (int)(10 * sinf(a)), INK); }
    }
    const uint16_t FR = spi->color565(238, 82, 60);               // フレーム(赤)
    cap(spi, bx - 18, (int)by, bx, (int)by - 4, 2, FR); cap(spi, bx, (int)by - 4, bx + 14, (int)by - 26, 2, FR); cap(spi, bx + 14, (int)by - 26, bx + 18, (int)by, 2, FR);
    cap(spi, bx - 18, (int)by, bx - 6, (int)by - 26, 2, FR); cap(spi, bx - 6, (int)by - 26, bx + 14, (int)by - 26, 2, FR);
    cap(spi, bx + 14, (int)by - 26, bx + 18, (int)by - 33, 2, INK);        // ハンドル
    int hipx = bx - 6, hipy = (int)by - 30;
    float pa = ang * 1.6f;
    for (int l = 0; l < 2; l++) {                                 // 足(ペダル)
      float a = pa + l * 3.14159f;
      int fx = bx + (int)(7 * cosf(a)), fy = (int)by - 4 + (int)(7 * sinf(a));
      cap(spi, hipx, hipy + 4, fx, fy, 3, l ? ZUN : spi->color565(60, 130, 50));
    }
    cap(spi, hipx, hipy, hipx + 5 + (int)tilt, hipy - 20, 7, ZUN);       // からだ
    cap(spi, hipx + 5, hipy - 17, bx + 16, (int)by - 34, 3, ZUN);        // うで
    int hx = hipx + 8 + (int)tilt, hy = hipy - 34;
    spi->fillCircle(hx, hy, 14, INK); spi->fillCircle(hx, hy, 12, SKIN);
    spi->fillCircle(hx - 2, hy - 5, 11, HAIR); spi->fillRect(hx - 13, hy - 1, 26, 8, SKIN);   // 髪(上半分)
    spi->fillCircle(hx - 12, hy + 1, 4, HAIR);
    cap(spi, hx, hy - 12, hx - 3, hy - 20, 2, HAIR); cap(spi, hx + 4, hy - 11, hx + 9, hy - 18, 2, HAIR);   // あほ毛
    if (phase == 2) { cap(spi, hx + 2, hy - 1, hx + 8, hy + 3, 1, INK); cap(spi, hx + 8, hy - 1, hx + 2, hy + 3, 1, INK); spi->drawFastHLine(hx + 3, hy + 8, 6, INK); }
    else { spi->fillCircle(hx + 5, hy + 1, 2, INK); spi->drawLine(hx + 2, hy + 7, hx + 8, hy + 7, INK); }
    // ---- 画面の文字
    spi->setFont(&fonts::efontJA_24); spi->setTextSize(1); spi->setTextDatum(TR_DATUM);
    char buf[24]; snprintf(buf, sizeof buf, "%um", (unsigned)(phase == 2 ? rec : (uint32_t)(dist / 12.0f)));
    spi->setTextColor(INK, SKY); spi->drawString(buf, 312, 26);
    spi->setTextDatum(MC_DATUM);
    if (phase == 0) {
      spi->setTextColor(INK, SKY); spi->setTextSize(2); spi->drawString(readyT < 0.9f ? "よーい…" : "ドン！", 160, 80);
    } else if (phase == 2) {
      spi->fillRoundRect(40, 54, 240, 90, 16, INK); spi->fillRoundRect(44, 58, 232, 82, 13, 0xFFFF);
      spi->setTextColor(INK, 0xFFFF); spi->setTextSize(1);
      spi->drawString("ガッシャーン！", 160, 80);
      snprintf(buf, sizeof buf, "%um はしった！", (unsigned)rec);
      spi->drawString(buf, 160, 108);
      if (overT > 0.8f) { spi->setFont(&fonts::efontJA_16); spi->setTextColor(FR, 0xFFFF); spi->drawString("タッチで もういちど", 160, 130); }
    }
    spi->setTextDatum(TL_DATUM);
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
    if (!b.nList && b.style) {                                    // ことば・しるしの吹き出し
      drawStyled(spi, b);
      return;
    }
    if (!b.nList && (b.aIcon || b.bIcon || b.aArrow || b.bArrow)) {   // 手・向きは、吹き出しのイラストで見せる
      drawBubbles(spi, b);
      return;
    }
    const int x0 = 6, y0 = 26, w = 308, h = b.nList ? 150 : 128;
    spi->fillRoundRect(x0, y0, w, h, 10, fg);
    spi->fillRoundRect(x0 + 2, y0 + 2, w - 4, h - 4, 9, bg);
    spi->setTextColor(fg, bg);
    spi->setTextDatum(TC_DATUM);
    spi->setFont(&fonts::efontJA_16);
    spi->setTextSize(1);
    if (b.title[0]) spi->drawString(b.title, x0 + w / 2, y0 + 5);
    if (b.nList) {                                             // 一覧: 2 列×3 行。長い名前は小さい字にする
      const int colW = w / 2 - 12, rowH = 36;
      for (int i = 0; i < b.nList && i < 6; i++) {
        int cx = x0 + (i % 2 ? 3 * w / 4 : w / 4), cy = y0 + 32 + (i / 2) * rowH;
        spi->setFont(&fonts::efontJA_24);
        spi->setTextSize(1);
        if (spi->textWidth(b.list[i]) > colW) spi->setFont(&fonts::efontJA_16);
        spi->drawString(b.list[i], cx, cy + 4);
        spi->drawRoundRect(x0 + (i % 2 ? w / 2 + 6 : 6), cy - 2, w / 2 - 12, rowH - 4, 8, fg);
      }
      if (b.foot[0]) {
        spi->setFont(&fonts::efontJA_16);
        spi->setTextSize(1);
        spi->drawString(b.foot, x0 + w / 2, y0 + h - 22);
      }
      spi->setTextDatum(TL_DATUM);
      return;
    }
    bool two = b.bBig[0] || b.bArrow || b.bWho[0];
    int colA = two ? x0 + w / 4 : x0 + w / 2, colB = x0 + 3 * w / 4;
    for (int c = 0; c < (two ? 2 : 1); c++) {
      const char *who = c ? b.bWho : b.aWho, *big = c ? b.bBig : b.aBig;
      uint8_t ar = c ? b.bArrow : b.aArrow;
      int cx = c ? colB : colA;
      spi->setFont(&fonts::efontJA_16);
      spi->setTextSize(1);
      if (who[0]) spi->drawString(who, cx, y0 + 28);
      uint8_t ic = c ? b.bIcon : b.aIcon;
      if (ic) {
        hand(spi, cx, y0 + 76, ic, fg, bg);
      } else if (ar) {
        arrow(spi, cx, y0 + 74, 26, ar, fg);
      } else if (big[0]) {
        spi->setFont(&fonts::efontJA_24);
        int len = strlen(big) / 3 + 1;                       // 文字数のめやす(日本語は 3 バイト)
        spi->setTextSize(len <= 3 || (two && len <= 4) ? 2 : 1);
        spi->drawString(big, cx, y0 + (spi->fontHeight() > 30 ? 50 : 62));
      }
    }
    if (two) spi->drawFastVLine(x0 + w / 2, y0 + 30, h - 60, fg);
    if (b.fx == 1) sparkle(spi, x0, y0, w, h, fg);
    if (b.foot[0]) {
      spi->setFont(&fonts::efontJA_16);
      spi->setTextSize(1);
      spi->drawString(b.foot, x0 + w / 2, y0 + h - 24);
    }
    spi->setTextDatum(TL_DATUM);
  }
  void draw(M5Canvas *spi, BoundingRect rect,
            DrawContext *drawContext) override {
    if (avatarChari.on) { chariDraw(spi); return; }         // ゲーム中は、ゲームの画面だけ(字幕も出さない)
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
