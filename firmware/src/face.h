// StackChan オリジナルの顔(m5stack-avatar)。main.cpp からは zface:: の同じ呼び出し口で使う。
// (自作のずんだもん風の顔は zunda_face.h に残してあるが、ビルドには使っていない)
#pragma once
#include <M5Unified.h>
#include <Avatar.h>

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

static void begin() {
  M5.Display.setRotation(1);
  avatar.setScale(0.9f);                                 // 上の 22px を状態バーに空ける
  avatar.setPosition(11, 0);
  avatar.init();
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
static uint32_t capUntil = 0;
static void keepCaption() { if (capUntil && capUntil < millis() + 6000) capUntil = millis() + 6000; }   // 読み上げ中は消さない
static void writeCaption(const char *t) {                // 排他つきで字幕を書く(描画タスクが同時に読んでも壊れない)
  m5avatar::avatarCaptionSeq++;                           // 奇数 = 書き込み中
  strlcpy(m5avatar::avatarCaption, t ? t : "", sizeof m5avatar::avatarCaption);
  m5avatar::avatarCaptionSeq++;
}
static void setCaption(const char *t) { writeCaption(t); capUntil = (t && *t) ? millis() + 14000 : 0; }   // 14秒たつと消える
static void update() {                                   // 表情・字幕の時間切れ(loop から呼ぶ)
  if (capUntil && millis() > capUntil) { capUntil = 0; writeCaption(""); }
  if (emoUntil && millis() > emoUntil) { emoUntil = 0; emo = NEUTRAL; apply(NEUTRAL); }
}

}  // namespace zface
