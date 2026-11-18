// StackChan オリジナルの顔(m5stack-avatar)。main.cpp からは zface:: の同じ呼び出し口で使う。
// (自作のずんだもん風の顔は zunda_face.h に残してあるが、ビルドには使っていない)
#pragma once
#include <M5Unified.h>
#include <Avatar.h>
#include <faces/FaceTemplates.hpp>   // M5Stack-Avatar 同梱の顔(MIT ライセンス): まる目・ガーリー・小悪魔・わんこ など
#include <faces/DogFace.h>
#include "zunda_image_face.h"      // ずんだもんのイラストの顔(口が動く)

namespace m5avatar { extern void (*avatarPostDraw)(); extern char avatarCaption[512]; extern volatile uint32_t avatarCaptionSeq; }   // lib/M5Stack-Avatar に追加したフック(顔の描画タスクから呼ばれる)

namespace zface {

enum Emo { NEUTRAL, HAPPY, THINK, SAD, SURPRISE, SLEEPY, LISTEN, GIGGLE };

static m5avatar::Avatar avatar;
static Emo emo = NEUTRAL;
static uint32_t emoUntil = 0;

static void apply(Emo e) {
  using m5avatar::Expression;
  switch (e) {
    case HAPPY: case GIGGLE: avatar.setExpression(Expression::Happy); break;
    case THINK:              avatar.setExpression(Expression::Doubt); break;
    case SAD:                avatar.setExpression(Expression::Sad); break;
    case SURPRISE:           avatar.setExpression(Expression::Angry); break;   // 目が大きく見える表情で代用
    case SLEEPY:             avatar.setExpression(Expression::Sleepy); break;
    default:                 avatar.setExpression(Expression::Neutral); break;
  }
}

// ---- 顔の種類(M5Stack-Avatar に同梱されている MIT ライセンスの顔と、その色)。ブリッジから {"t":"faceStyle","id":N} で切り替える ----
static const int STYLE_N = 8;
static const char *STYLE_NAME[STYLE_N] = {"標準", "まる目(OωO)", "ガーリー", "ガーリー(リボン)", "ピンクの小悪魔", "わんこ", "シンプル(o_o)", "ずんだもん(イラスト)"};
static m5avatar::Face *styleFace[STYLE_N] = {nullptr};   // 一度作ったら使い回す(切り替えるたびに作らない)
static int styleNow = 0;
static void setStyle(int id) {
  using namespace m5avatar;
  if (id < 0 || id >= STYLE_N) id = 0;
  if (!styleFace[0]) return;                             // begin() 前
  if (!styleFace[id]) {
    switch (id) {
      case 1: styleFace[id] = new OmegaFace(); break;
      case 2: styleFace[id] = new GirlyFace(); break;
      case 3: styleFace[id] = new GirlyFace2(); break;
      case 4: styleFace[id] = new PinkDemonFace(); break;
      case 5: styleFace[id] = new DogFace(); break;
      case 6: styleFace[id] = new SimpleFace(); break;
      case 7: styleFace[id] = new ZundaArtFace(); break;
      default: styleFace[id] = styleFace[0]; break;
    }
  }
  static const uint8_t RGB[STYLE_N][3] = {{255, 255, 255}, {150, 235, 150}, {255, 150, 200}, {200, 160, 255}, {255, 100, 170}, {255, 190, 110}, {120, 220, 255}, {255, 255, 255}};
  ColorPalette cp;
  cp.set(COLOR_PRIMARY, M5.Display.color565(RGB[id][0], RGB[id][1], RGB[id][2]));
  cp.set(COLOR_BACKGROUND, id == 7 ? M5.Display.color565(ZART_BG[0], ZART_BG[1], ZART_BG[2]) : 0x0000);   // イラストの顔は背景も緑
  avatar.setColorPalette(cp);
  avatar.setColorDepth(id == 7 ? 16 : 1);                  // イラストはカラー、ほかは白黒の顔(色はパレット)
  avatar.setFace(styleFace[id]);
  styleNow = id;
}

static void begin() {
  M5.Display.setRotation(1);
  avatar.setScale(0.9f);                                 // 上の 22px を状態バーに空ける
  avatar.setPosition(11, 0);
  avatar.init();
  styleFace[0] = avatar.getFace();                       // 初期の顔(標準)を覚えておく
  M5.Display.setClipRect(0, 22, M5.Display.width(), M5.Display.height() - 22);   // 顔の描画が状態バーを消さない
  avatar.setSpeechFont(&fonts::efontJA_16);
  apply(NEUTRAL);
}
// 字幕の言語に合わせた字体(日本語の字体だと簡体字・ハングルが出ない)
static void setCaptionLang(const char *lang) {
  if (!strcmp(lang, "zh")) avatar.setSpeechFont(&fonts::efontCN_16);
  else if (!strcmp(lang, "ko")) avatar.setSpeechFont(&fonts::efontKR_16);
  else avatar.setSpeechFont(&fonts::efontJA_16);
}
static void setEmotion(Emo e, uint32_t holdMs = 0) { emo = e; emoUntil = holdMs ? millis() + holdMs : 0; apply(e); }
static void setMouth(float open, float wide) { (void)wide; avatar.setMouthOpenRatio(constrain(open, 0.f, 1.f)); }
static void setGaze(float x, float y) {
  float v = constrain(y, -1.f, 1.f), h = constrain(x, -1.f, 1.f);
  avatar.setRightGaze(v, h); avatar.setLeftGaze(v, h);
}
static void setSpeaking(bool s) { if (!s) avatar.setMouthOpenRatio(0); }
static uint32_t capUntil = 0, boardUntil = 0;
static void clearBoard() {
  m5avatar::avatarBoardSeq++;
  m5avatar::avatarBoard.on = false;
  m5avatar::avatarBoardSeq++;
  boardUntil = 0;
}
// 遊びの表示カードを出す(ms 後に消える)。arrow: 0=なし 1=上 2=下 3=左 4=右
static void setBoard(const char *title, const char *aWho, const char *aBig, int aArrow, const char *bWho, const char *bBig, int bArrow, const char *foot, uint32_t ms, int aIcon = 0, int bIcon = 0, int fx = 0) {
  m5avatar::avatarBoardSeq++;
  auto &b = m5avatar::avatarBoard;
  strlcpy(b.title, title ? title : "", sizeof b.title);
  strlcpy(b.aWho, aWho ? aWho : "", sizeof b.aWho);
  strlcpy(b.aBig, aBig ? aBig : "", sizeof b.aBig);
  strlcpy(b.bWho, bWho ? bWho : "", sizeof b.bWho);
  strlcpy(b.bBig, bBig ? bBig : "", sizeof b.bBig);
  strlcpy(b.foot, foot ? foot : "", sizeof b.foot);
  b.aArrow = (uint8_t)aArrow; b.bArrow = (uint8_t)bArrow;
  b.aIcon = (uint8_t)aIcon; b.bIcon = (uint8_t)bIcon; b.fx = (uint8_t)fx;
  b.nList = 0;
  b.on = true;
  m5avatar::avatarBoardSeq++;
  boardUntil = millis() + (ms ? ms : 6000);
}
static void keepCaption() { if (capUntil && capUntil < millis() + 6000) capUntil = millis() + 6000; }   // 読み上げ中は消さない
static void writeCaption(const char *t) {                // 排他つきで字幕を書く(描画タスクが同時に読んでも壊れない)
  m5avatar::avatarCaptionSeq++;                           // 奇数 = 書き込み中
  strlcpy(m5avatar::avatarCaption, t ? t : "", sizeof m5avatar::avatarCaption);
  m5avatar::avatarCaptionSeq++;
}
static void setCaption(const char *t) { writeCaption(t); capUntil = (t && *t) ? millis() + 14000 : 0; }   // 14秒たつと消える
// 一覧のカード(言える遊びの名前)。items は最大 6 つ
static void setBoardList(const char *title, const char *const *items, int n, const char *foot, uint32_t ms) {
  m5avatar::avatarBoardSeq++;
  auto &b = m5avatar::avatarBoard;
  strlcpy(b.title, title ? title : "", sizeof b.title);
  b.aWho[0] = b.aBig[0] = b.bWho[0] = b.bBig[0] = 0;
  strlcpy(b.foot, foot ? foot : "", sizeof b.foot);
  b.aArrow = b.bArrow = b.aIcon = b.bIcon = b.fx = 0;
  b.nList = (uint8_t)(n > 6 ? 6 : n);
  for (int i = 0; i < b.nList; i++) strlcpy(b.list[i], items[i] ? items[i] : "", sizeof b.list[i]);
  b.on = true;
  m5avatar::avatarBoardSeq++;
  boardUntil = millis() + (ms ? ms : 6000);
}
static void update() {                                   // 表情・字幕の時間切れ(loop から呼ぶ)
  if (capUntil && millis() > capUntil) { capUntil = 0; writeCaption(""); }
  if (boardUntil && millis() > boardUntil) clearBoard();
  if (emoUntil && millis() > emoUntil) { emoUntil = 0; emo = NEUTRAL; apply(NEUTRAL); }
}

}  // namespace zface
