// ずんだもんのイラストの顔。絵(口は消してある)を背景に出し、口とまばたきを機器の上で描く。
// 口は音声の大きさ(口の開き)に合わせて動き、まばたき・わらった目・しょんぼりの口は表情に合わせる。
// 絵のデータ(zunda_art.h)は tools/make_zunda_art.py が作る。イラストの権利は元の作者にある(利用ガイドラインを確認すること)。
#pragma once
#include <M5Unified.h>
#include <Avatar.h>
#include "zunda_art.h"

namespace m5avatar {

class ZundaArtNull : public Drawable {
 public:
  void draw(M5Canvas *, BoundingRect, DrawContext *) override {}
};

class ZundaArtDraw : public Drawable {
  static void eyeClosed(M5Canvas *s, int ex, int ey, bool happy, uint16_t ol, uint16_t skin) {
    int rx = ZART_EYE_RX * 7 / 10, ry = ZART_EYE_RY * 6 / 10;
    int cy = happy ? ey + 5 : ey - 3;
    s->fillEllipse(ex, ey, ZART_EYE_RX, ZART_EYE_RY, skin);                          // 目をおおう
    s->fillEllipse(ex, cy, rx, ry, ol);                                               // 線(輪)
    s->fillEllipse(ex, cy, rx - 3, ry - 3, skin);
    if (happy) s->fillRect(ex - rx - 1, cy, rx * 2 + 3, ry + 2, skin);                // 下半分を消して「⌒」
    else       s->fillRect(ex - rx - 1, cy - ry - 2, rx * 2 + 3, ry + 2, skin);       // 上半分を消して「︶」
  }

 public:
  void draw(M5Canvas *s, BoundingRect rect, DrawContext *ctx) override {
    const int ox = (320 - ZART_SIZE) / 2, oy = 30 + (int)rect.getTop();             // rect の top は息づかいのゆれ
    const uint16_t ol = s->color565(70, 45, 50), red = s->color565(209, 126, 127), skin = s->color565(ZART_SKIN[0], ZART_SKIN[1], ZART_SKIN[2]);
    s->pushImage(ox, oy, ZART_SIZE, ZART_SIZE, (const lgfx::rgb565_t *)ZART_PX);

    Expression ex = ctx->getExpression();
    bool happy = ex == Expression::Happy, sad = ex == Expression::Sad;
    // 目: まばたき(開き具合が小さい)・わらった目
    float eorR = ctx->getRightEyeOpenRatio(), eorL = ctx->getLeftEyeOpenRatio();
    for (int k = 0; k < 2; k++) {
      float eor = k == 0 ? eorL : eorR;
      if (happy || sad == false ? (happy || eor < 0.35f) : eor < 0.35f) eyeClosed(s, ox + ZART_EYE_X[k], oy + ZART_EYE_Y[k], happy, ol, skin);
    }
    // 口: 開き具合(0〜1)に合わせて、閉じた口 → 小さく開く → 大きく開く
    float r = ctx->getMouthOpenRatio();
    if (happy && r < 0.55f) r = 0.55f;                                                // わらっているときは、ふだんから少し開ける
    const int cx = ox + ZART_MOUTH_CX, top = oy + ZART_MOUTH_TOP;
    if (r < 0.1f) {
      if (sad) {                                                                      // しょんぼり: 「︵」
        s->fillEllipse(cx, top + 9, 7, 5, ol);
        s->fillEllipse(cx, top + 9, 5, 3, skin);
        s->fillRect(cx - 8, top + 9, 17, 7, skin);
      } else {                                                                        // 閉じた口: 小さな「︶」
        s->fillEllipse(cx, top + 5, 7, 6, ol);
        s->fillEllipse(cx, top + 5, 5, 4, skin);
        s->fillRect(cx - 8, top - 2, 17, 7, skin);
      }
    } else {
      if (r > 1.0f) r = 1.0f;
      int w = 16 + (int)(8 * r), h = 7 + (int)(14 * r);
      s->fillRect(cx - w / 2, top, w + 1, h / 2, ol);
      s->fillEllipse(cx, top + h / 2, w / 2, h / 2, ol);
      s->fillRect(cx - w / 2 + 2, top + 2, w - 3, h / 2 - 1, red);
      s->fillEllipse(cx, top + h / 2, w / 2 - 2, h / 2 - 2, red);
    }
  }
};

class ZundaArtFace : public Face {
 public:
  ZundaArtFace()
      : Face(new ZundaArtDraw(), new BoundingRect(0, 0), new ZundaArtNull(), new BoundingRect(0, 0), new ZundaArtNull(), new BoundingRect(0, 0),
             new ZundaArtNull(), new BoundingRect(0, 0), new ZundaArtNull(), new BoundingRect(0, 0)) {}
};

}  // namespace m5avatar
