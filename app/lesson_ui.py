"""管理アプリの「授業」画面: 先生が、機器ごとに遊びを始める・中止する・切り替える。

ロボットは、ここでボタンが押されるまで何もしない(授業モード)。ボタンは大きく、押したらすぐ反応する。
  - 機器ごとのカード: 6 つの遊びのボタン / ■ 中止(どの遊びでも、すぐ止まる) / いまの状態 / 先生の入力
  - 全台のボタン: 全部の機器で、いっせいに始める・中止する
動いている遊びがあるときに、別の遊びのボタンを押すと、いまの遊びを止めて、すぐ切り替わる。
状態は、ブリッジの state["lesson"](機器ごとの、いまの遊びと詳細)を描くだけ。通信は act(name, body) に任せる。
"""
from __future__ import annotations

import html
import json

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF, QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

CY, GR, AM, RD, MUTE = "#3fe0ff", "#41ffa8", "#ffc14d", "#ff5a6e", "#6f8aa6"
KINDS = [("shiritori", "しりとり"), ("janken", "じゃんけん"), ("riddle", "なぞなぞ"), ("omikuji", "おみくじ"), ("mimic", "ものまねボイス"), ("chari", "チャリはしり")]
LABEL = dict(KINDS)
HINT = {"shiritori": "言葉を言う(例: りんご)", "janken": "ぐー・ちょき・ぱー", "riddle": "答えを言う(「ヒント」で ヒント)",
        "hoi": "ロボットが ゆびさした向きと、ちがう向きを向く / 子どもの番は、うえ・した・ひだり・みぎ を言って ゆびさす", "omikuji": "やる / やめる", "mimic": "まねしてほしい言葉", "chari": "子どもは機器の画面をタッチしてジャンプ(声で「ジャンプ」でも可)。ぶつかったら、またタッチで もういちど"}
BTN_OFF = "QPushButton{font-size:17px;font-weight:700;padding:14px 8px;min-height:30px}"
BTN_ON = f"QPushButton{{font-size:17px;font-weight:700;padding:14px 8px;min-height:30px;border:2px solid {GR};color:{GR};background:rgba(65,255,168,40)}}"
STOP_QSS = f"QPushButton{{font-size:20px;font-weight:700;padding:16px 8px;border:2px solid {RD};color:#fff;background:{RD}}}QPushButton:disabled{{border:1px solid #3a2a30;color:#6a5258;background:rgba(255,90,110,10)}}QPushButton:hover:!disabled{{background:#ff7d8c}}"


def kind_has_art(kind: str) -> bool:
    return kind in ("janken", "hoi")


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def status_of(x: dict) -> "tuple[str, str]":
    """(大きな表示, 色)。x は lesson の 1 機器ぶん。"""
    if not x.get("online"):
        return "つながっていません", MUTE
    if not x.get("on"):
        d = x.get("detail") or {}
        if d.get("result"):
            return "おわりました(待機中)", MUTE
        return "待機中 — ロボットは何もしません", MUTE
    d = x.get("detail") or {}
    lab = x.get("label") or ""
    if d.get("turn") == "kid":
        return f"{lab}: 子どもの番(聞いています)", GR
    who = x.get("persona") or "ロボット"
    return f"{lab}: {who}が話しています", CY


def detail_html(x: dict) -> str:
    """いまの遊びの様子(先生向けの、小さめの表示)。"""
    d = x.get("detail") or {}
    kind = x.get("kind") or ""
    lines = []
    if d.get("msg"):
        lines.append(f'<span style="font-size:15px">{esc(d["msg"])}</span>')
    if kind == "shiritori" or (not kind and d.get("chain")):
        ch = d.get("chain") or []
        if x.get("on") and d.get("need"):
            lines.append(f'つぎは「<b style="color:{AM}">{esc(d["need"])}</b>」から')
        if ch:
            lines.append(" → ".join(f'<span style="color:{GR if c.get("kid") else AM}">{esc(c.get("word"))}</span>' for c in ch[-8:]) + f'<br><span style="color:{MUTE}">つないだ数 {esc(d.get("score", 0))}</span>')
    elif kind == "janken" or d.get("hands") is not None:
        hs = d.get("hands") or []
        lines.append(f'<span style="color:{MUTE}">勝ち {esc(d.get("wins", 0))} / 負け {esc(d.get("losses", 0))} / あいこ {esc(d.get("draws", 0))}</span>')
        for h in reversed(hs[-4:]):
            col = {"kid_win": GR, "robot_win": AM}.get(h.get("res"), MUTE)
            lines.append(f'<span style="color:{col}">きみ {esc(h.get("kid"))}{"(カメラ)" if h.get("via") == "camera" else ""} / ロボット {esc(h.get("robot"))}</span>')
    else:
        if x.get("on") and d.get("big"):
            lines.append(f'<span style="font-size:24px;color:{AM}"><b>{esc(d["big"])}</b></span>')
        if x.get("on") and d.get("question"):
            lines.append(esc(d["question"]))
        if x.get("on") and d.get("answer"):
            lines.append(f'<span style="color:{MUTE}">先生用: {esc(d["answer"])}</span>')
        if d.get("stat"):
            lines.append(f'<span style="color:{GR}">{esc(d["stat"])}</span>')
        for h in reversed((d.get("history") or [])[-4:]):
            lines.append(f'<span style="color:{GR if h.get("ok") else AM}">{"○" if h.get("ok") else "×"}</span> {esc(h.get("q"))}')
    return "<br>".join(lines)


def camera_html(x: dict) -> str:
    """カメラの見え方(距離の案内)。カメラを使う遊びのときだけ。"""
    d = x.get("detail") or {}
    if not x.get("on") or not d.get("cam_label"):
        return ""
    st = d.get("cam_state") or ""
    col = GR if st == "ok" else AM
    note = ' <span style="color:%s">(見えにくいので、声だけで進めています)</span>' % AM if d.get("cam_muted") else ""
    return f'カメラ: <b style="color:{col}">{esc(d["cam_label"])}</b>{note}'


ICON = {"gu": 1, "choki": 2, "pa": 3}
ARROW = {"up": 1, "down": 2, "left": 3, "right": 4}


class BubbleView(QWidget):
    """機器の画面と同じ、吹き出しのイラスト(ずんだもんの手・きみの手・向きの矢印)。board は ブリッジの board_state。"""
    INK, SKIN, SHADE = QColor(40, 40, 52), QColor(255, 214, 168), QColor(226, 170, 120)

    def __init__(self):
        super().__init__()
        self.board: "dict | None" = None
        self.setMinimumHeight(150)

    def set_board(self, b: "dict | None") -> None:
        if b != self.board:
            self.board = b
            self.update()

    # ---- 部品(機器の Balloon.h と同じ形)
    def cap(self, p, x0, y0, x1, y1, r, col):
        p.setPen(QPen(col, 2 * r, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def hand(self, p, cx, cy, icon, s):
        def P(v): return v * s
        def rr(x, y, w, h, r, g, c):
            p.setPen(Qt.NoPen); p.setBrush(c)
            p.drawRoundedRect(QRectF(cx + P(x) - g, cy + P(y) - g, P(w) + 2 * g, P(h) + 2 * g), P(r) + g, P(r) + g)
        def fg(x0, y0, x1, y1, r, g, c): self.cap(p, cx + P(x0), cy + P(y0), cx + P(x1), cy + P(y1), P(r) + g, c)
        def line(x0, y0, x1, y1):
            p.setPen(QPen(self.SHADE, 1.2)); p.drawLine(QPointF(cx + P(x0), cy + P(y0)), QPointF(cx + P(x1), cy + P(y1)))
        def shape(g, c):
            if icon == 1:
                rr(-35, -16, 70, 54, 20, g, c)
                for i in range(4): rr(-34 + i * 17, -24, 17, 32, 8, g, c)
                fg(-27, 20, 14, 20, 9, g, c)
            elif icon == 2:
                rr(-28, 6, 56, 42, 16, g, c); fg(-11, 12, -20, -38, 8, g, c); fg(11, 12, 20, -38, 8, g, c); fg(-28, 30, 2, 28, 8, g, c)
            elif icon == 3:
                rr(-30, 8, 60, 44, 18, g, c)
                fg(-23, 14, -28, -32, 7.5, g, c); fg(-8, 12, -10, -43, 7.5, g, c); fg(8, 12, 10, -43, 7.5, g, c); fg(23, 14, 28, -32, 7.5, g, c); fg(-29, 36, -47, 12, 8, g, c)
        if icon not in (1, 2, 3):
            return
        shape(3, self.INK); shape(0, self.SKIN)
        if icon == 1:
            for i in range(1, 4): line(-34 + i * 17, -18, -34 + i * 17, -2)
            line(-26, 11, 12, 11)
        elif icon == 2:
            line(14, 16, 14, 30); line(22, 16, 22, 30); line(-24, 22, 4, 20)
        else:
            for i in (-1, 0, 1): line(i * 16, -14, i * 16, 12)

    def arrow(self, p, cx, cy, d, s, col):
        def mp(x, y):
            X, Y = x * s, y * s
            return {1: (X, Y), 2: (X, -Y), 3: (Y, X)}.get(d, (-Y, X))
        def poly(ox, oy, c):
            pts = [mp(-12, -2), mp(12, -2), mp(12, 36), mp(-12, 36)]
            head = [mp(-32, -4), mp(32, -4), mp(0, -44)]
            p.setPen(Qt.NoPen); p.setBrush(c)
            for pg in (pts, head):
                p.drawPolygon(QPolygonF([QPointF(cx + ox + a, cy + oy + b) for a, b in pg]))
        for dx in (-3, 0, 3):
            for dy in (-3, 0, 3):
                if dx or dy: poly(dx, dy, self.INK)
        poly(0, 0, col)

    def bubble(self, p, x, y, w, h, tx, ty, edge):
        bx = x + 26 if tx < x + w / 2 else x + w - 26
        p.setPen(Qt.NoPen); p.setBrush(edge)
        p.drawPolygon(QPolygonF([QPointF(bx - 15, y + h - 3), QPointF(bx + 15, y + h - 3), QPointF(tx, ty)]))
        p.drawRoundedRect(QRectF(x, y, w, h), 18, 18)
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(x + 4, y + 4, w - 8, h - 8), 15, 15)
        p.drawPolygon(QPolygonF([QPointF(bx - 10, y + h - 7), QPointF(bx + 10, y + h - 7), QPointF(tx, ty - 7)]))


    # ---- ことば・しるしの吹き出し(しりとり・なぞなぞ・おみくじ・ものまね。機器の Balloon.h と同じ)
    def mark(self, p, cx, cy, kind, s):
        r = 26 * s
        red, blu, gold, zun = QColor(238, 82, 60), QColor(40, 110, 220), QColor(255, 200, 40), QColor(86, 176, 64)
        p.setPen(Qt.NoPen)
        if kind == "ok":
            p.setBrush(red); p.drawEllipse(QPointF(cx, cy), r, r); p.setBrush(QColor(255, 255, 255)); p.drawEllipse(QPointF(cx, cy), r - 9 * s, r - 9 * s)
        elif kind == "ng":
            self.cap(p, cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4, 6 * s, blu); self.cap(p, cx + r - 4, cy - r + 4, cx - r + 4, cy + r - 4, 6 * s, blu)
        elif kind == "q":
            p.setBrush(zun); p.drawEllipse(QPointF(cx, cy), r, r)
            f = QFont(); f.setPixelSize(int(34 * s)); f.setBold(True); p.setFont(f); p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, "?")
        elif kind == "star":
            import math
            R = r * 1.15
            p.setBrush(gold)
            p.drawPolygon(QPolygonF([QPointF(cx + (R if i % 2 == 0 else R * 0.45) * math.cos(math.radians(-90 + 36 * i)), cy + (R if i % 2 == 0 else R * 0.45) * math.sin(math.radians(-90 + 36 * i))) for i in range(10)]))
        elif kind == "note":
            p.setBrush(self.INK); p.drawEllipse(QPointF(cx - r / 3, cy + r / 2), r / 2, r / 2)
            p.drawRect(QRectF(cx - r / 3 + r / 2 - 3, cy - r, 5, r * 1.5 + 2))
            p.drawPolygon(QPolygonF([QPointF(cx + r / 6 + 2, cy - r), QPointF(cx + r / 6 + 2, cy - r / 4), QPointF(cx + r, cy - r / 8)]))

    def content(self, p, x, y, w, h, mk, text, hl):
        if mk and not text:
            self.mark(p, x + w / 2, y + h / 2, mk, 1.5); return
        tx, tw = x + 10, w - 20
        if mk:
            self.mark(p, x + 34, y + h / 2, mk, 0.9); tx, tw = x + 66, w - 76
        if not text:
            return
        f = QFont(); f.setPixelSize(24); p.setFont(f)
        if p.fontMetrics().horizontalAdvance(text) > tw:
            f.setPixelSize(16); p.setFont(f)
        fm = p.fontMetrics()
        if fm.horizontalAdvance(text) <= tw:
            x1 = tx if mk else tx + (tw - fm.horizontalAdvance(text)) / 2
            head, last = (text[:-1], text[-1]) if hl else (text, "")
            p.setPen(self.INK); p.drawText(QPointF(x1, y + h / 2 + fm.ascent() / 2 - 1), head)
            if last:
                p.setPen(QColor(238, 82, 60)); p.drawText(QPointF(x1 + fm.horizontalAdvance(head), y + h / 2 + fm.ascent() / 2 - 1), last)
            return
        a = ""
        for ch in text:
            if fm.horizontalAdvance(a + ch) > tw: break
            a += ch
        rest = text[len(a):]
        while fm.horizontalAdvance(rest) > tw and len(rest) > 1: rest = rest[:-1]
        p.setPen(self.INK)
        p.drawText(QPointF(tx, y + h / 2 - 11 + fm.ascent() / 2), a); p.drawText(QPointF(tx, y + h / 2 + 11 + fm.ascent() / 2), rest)

    def paint_styled(self, p, b):
        zun, sky, red, gold = QColor(86, 176, 64), QColor(64, 144, 224), QColor(238, 82, 60), QColor(255, 200, 40)
        st, foot = b.get("st"), b.get("foot") or ""
        if st != "omikuji" and foot and len(foot) <= 16:
            f = QFont(); f.setPixelSize(15); f.setBold(True); p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(foot) + 28
            p.setPen(QPen(self.INK, 2)); p.setBrush(gold)
            p.drawRoundedRect(QRectF(160 - tw / 2, 24, tw, 24), 12, 12)
            p.drawText(QRectF(160 - tw / 2, 24, tw, 24), Qt.AlignCenter, foot)
        top, hh = 54, 98
        if st == "omikuji":
            paper = QColor(255, 250, 236)
            p.setPen(Qt.NoPen); p.setBrush(red); p.drawRoundedRect(QRectF(40, 28, 240, 124), 14, 14)
            p.setBrush(paper); p.drawRoundedRect(QRectF(44, 32, 232, 116), 11, 11)
            f = QFont(); f.setPixelSize(16); p.setFont(f); p.setPen(red)
            p.drawText(QRectF(40, 36, 240, 20), Qt.AlignCenter, "きょうの うんせい")
            f = QFont(); f.setPixelSize(48); f.setBold(True); p.setFont(f); p.setPen(self.INK)
            p.drawText(QRectF(40, 54, 240, 56), Qt.AlignCenter, b.get("ab") or "")
            cc = b.get("cc") or ""
            if cc:
                p.setPen(QPen(self.INK, 2)); p.setBrush(QColor("#" + cc)); p.drawEllipse(QPointF(66, 130), 10, 10)
            f = QFont(); f.setPixelSize(16); p.setFont(f); p.setPen(self.INK)
            p.drawText(QRectF(84, 120, 190, 20), Qt.AlignVCenter | Qt.AlignLeft, foot)
            if b.get("am") == "star":
                self.mark(p, 60, 56, "star", 0.5); self.mark(p, 260, 56, "star", 0.5)
            return
        ha, hb = bool(b.get("ab") or b.get("am")), bool(b.get("bb") or b.get("bm"))
        hl = bool(b.get("hl"))
        def side(x, w, edge, tx, mk, t, h):
            self.bubble(p, x, top, w, hh, tx, top + hh + 12, edge)
            self.content(p, x, top, w, hh, mk, t, h)
        if ha and hb:
            side(6, 152, zun, 120, b.get("am"), b.get("ab"), hl)
            side(162, 152, sky, 280, b.get("bm"), b.get("bb"), hl and st == "word")
            f = QFont(); f.setPixelSize(15); f.setBold(True); p.setFont(f); p.setPen(self.INK)
            p.drawText(QRectF(162, top + 6, 152, 20), Qt.AlignCenter, "きみ")
        elif ha:
            side(40, 240, zun, 138, b.get("am"), b.get("ab"), hl)
        elif hb:
            side(40, 240, sky, 200, b.get("bm"), b.get("bb"), hl)

    def paintEvent(self, _e):
        b = self.board
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not b:
            p.setPen(QColor(MUTE))
            p.drawText(self.rect(), Qt.AlignCenter, "ここに、出した手や向きの絵が出ます")
            return
        W, H, Y0 = 320.0, 150.0, 20.0                              # 機器の画面と同じ座標(320 幅、y=20〜170)
        k = min(self.width() / W, self.height() / H)
        p.translate((self.width() - W * k) / 2, (self.height() - H * k) / 2)
        p.scale(k, k)
        p.translate(0, -Y0)
        if b.get("st"):
            self.paint_styled(p, b)
            return
        zun, sky, red, blu, gold = QColor(86, 176, 64), QColor(64, 144, 224), QColor(238, 82, 60), QColor(40, 110, 220), QColor(255, 200, 40)
        ai, bi = ICON.get(b.get("ai"), 0), ICON.get(b.get("bi"), 0)
        aa, ba = ARROW.get(b.get("aa"), 0), ARROW.get(b.get("ba"), 0)
        has_a, has_b = bool(ai or aa), bool(bi or ba)
        foot = b.get("foot") or ""
        if foot and len(foot) <= 16:
            f = QFont(); f.setPixelSize(15); f.setBold(True); p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(foot) + 28
            p.setPen(QPen(self.INK, 2)); p.setBrush(gold)
            p.drawRoundedRect(QRectF(160 - tw / 2, 24, tw, 24), 12, 12)
            p.drawText(QRectF(160 - tw / 2, 24, tw, 24), Qt.AlignCenter, foot)
        top, hh = 54, 98
        def side(x, w, icon, arrow, tx, edge, acol, s, dy):
            self.bubble(p, x, top, w, hh, tx, top + hh + 12, edge)
            if icon: self.hand(p, x + w / 2, top + hh / 2 + dy, icon, s)
            else: self.arrow(p, x + w / 2, top + hh / 2 - 2 + dy, arrow, s * 0.9, acol)
        if has_a and has_b:
            side(8, 170, ai, aa, 138, zun, red, 0.8, 0)
            side(188, 124, bi, ba, 290, sky, blu, 0.6, 9)
            f = QFont(); f.setPixelSize(15); f.setBold(True); p.setFont(f); p.setPen(self.INK)
            p.drawText(QRectF(188, top + 6, 124, 20), Qt.AlignCenter, "きみ")
        elif has_a:
            side(70, 180, ai, aa, 138, zun, red, 0.88, 0)
        elif has_b:
            side(70, 180, bi, ba, 200, sky, blu, 0.92, 0)


class DeviceCard(QFrame):
    def __init__(self, dev_id: str, act):
        super().__init__()
        self.dev_id, self.act = dev_id, act
        self.setObjectName("lcard")
        self.setStyleSheet("#lcard{background:rgba(10,20,34,0.6);border:1px solid #1d3a55;border-radius:8px}#lcard QLabel{background:transparent}")
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.head.setStyleSheet("font-size:18px")
        v.addWidget(self.head)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setMinimumHeight(54)
        v.addWidget(self.status)
        g = QGridLayout()
        g.setSpacing(8)
        self.b: dict = {}
        for n, (k, lab) in enumerate(KINDS):
            b = QPushButton(lab)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(BTN_OFF)
            b.clicked.connect(lambda _c=False, k=k: self._go(k))
            g.addWidget(b, n // 2, n % 2)
            self.b[k] = b
        v.addLayout(g)
        self.stop = QPushButton("■ 中止")
        self.stop.setCursor(Qt.PointingHandCursor)
        self.stop.setStyleSheet(STOP_QSS)
        self.stop.clicked.connect(self._halt)
        v.addWidget(self.stop)
        self.cam = QLabel()
        self.cam.setTextFormat(Qt.RichText)
        self.cam.setStyleSheet("font-size:15px")
        v.addWidget(self.cam)
        self.bub = BubbleView()
        v.addWidget(self.bub)
        self.body = QLabel()
        self.body.setTextFormat(Qt.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setMinimumHeight(110)
        v.addWidget(self.body)
        self.typ = QLineEdit()
        self.typ.setPlaceholderText("先生の入力(聞き取れないとき): 子どもの言葉を入れて Enter")
        self.typ.returnPressed.connect(self._type)
        v.addWidget(self.typ)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{MUTE};font-size:12px")
        v.addWidget(self.hint)
        v.addStretch(1)

    def _halt(self) -> None:
        self.status.setText(f'<span style="font-size:20px;font-weight:700;color:{RD}">中止しています…</span>')
        self.act("lesson_stop", {"id": self.dev_id}, "中止しました")

    def _go(self, k: str) -> None:
        self.status.setText(f'<span style="font-size:20px;font-weight:700;color:{AM}">{esc(LABEL[k])}を はじめています…</span>')       # 押した瞬間に反応を見せる
        self.act("lesson_start", {"kind": k, "id": self.dev_id}, f"{LABEL[k]}を始めます")

    def _type(self) -> None:
        t = self.typ.text().strip()
        if t:
            self.act("lesson_say", {"text": t, "id": self.dev_id})
            self.typ.clear()

    def render(self, x: dict) -> None:
        persona = x.get("persona") or ""
        self.head.setText(f'<b>{esc(x.get("name"))}</b>' + (f' <span style="color:{AM}">({esc(persona)})</span>' if persona else ""))
        big, col = status_of(x)
        self.status.setText(f'<span style="font-size:20px;font-weight:700;color:{col}">{esc(big)}</span>')
        online, on, cur = bool(x.get("online")), bool(x.get("on")), x.get("kind") or ""
        for k, b in self.b.items():
            b.setEnabled(online)
            b.setStyleSheet(BTN_ON if (on and k == cur) else BTN_OFF)
        self.stop.setEnabled(online and on)
        self.cam.setText(camera_html(x))
        self.bub.set_board(x.get("board") if on else None)
        self.bub.setVisible(bool(on and kind_has_art(cur)))
        self.body.setText(detail_html(x))
        self.hint.setText(("子どもに言ってもらうこと: " + HINT.get(cur, "")) if on and cur else "")
        self.typ.setEnabled(online and on)


class LessonPage(QWidget):
    """act(name, body, ok_msg) は、ブリッジへの操作(管理アプリの act)。"""

    def __init__(self, act):
        super().__init__()
        self.act = act
        self.cards: dict = {}
        self.key = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.mode = QCheckBox("授業モード  (オン: 先生がボタンを押すまで、ロボットは何も話さない・何もしない。翻訳・3人会話・自律会話は止まる)")
        self.mode.setStyleSheet(f"font-size:15px;font-weight:700;color:{AM};padding:6px")
        self.mode.toggled.connect(self._mode)
        lay.addWidget(self.mode)

        self.ready = QLabel("")
        self.ready.setTextFormat(Qt.RichText)
        self.ready.setWordWrap(True)
        lay.addWidget(self.ready)

        row = QHBoxLayout()
        row.setSpacing(8)
        lab = QLabel("全台でいっせいに:")
        lab.setStyleSheet("font-size:14px;font-weight:700")
        row.addWidget(lab)
        for k, t in KINDS:
            b = QPushButton(t)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton{font-size:14px;padding:10px 12px}")
            b.clicked.connect(lambda _c=False, k=k: self.act("lesson_start", {"kind": k}, f"全台で{LABEL[k]}を始めます"))
            row.addWidget(b)
        row.addStretch(1)
        self.stop_all = QPushButton("■ 全部中止")
        self.stop_all.setCursor(Qt.PointingHandCursor)
        self.stop_all.setStyleSheet(STOP_QSS)
        self.stop_all.clicked.connect(lambda: self.act("lesson_stop", {}, "全部、中止しました"))
        row.addWidget(self.stop_all)
        lay.addLayout(row)

        self.wrap = QWidget()
        self.wrap.setObjectName("lwrap")
        self.wrap.setStyleSheet("#lwrap{background:transparent}")
        self.hl = QHBoxLayout(self.wrap)
        self.hl.setContentsMargins(0, 0, 0, 0)
        self.hl.setSpacing(10)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setStyleSheet("QScrollArea{background:transparent;border:0}")
        sa.viewport().setStyleSheet("background:transparent")
        sa.setWidget(self.wrap)
        lay.addWidget(sa, 1)
        foot = QLabel("機器どうしは連携しません。各機器が、自分の前の子どもたちと遊びます。ボタンを押すと、その機器でいますぐ始まります(動いている遊びは止めて切り替えます)。「■ 中止」を押すと、声もすぐ止まります。")
        foot.setWordWrap(True)
        foot.setStyleSheet(f"color:{MUTE};font-size:12px")
        lay.addWidget(foot)

    def _mode(self, on: bool) -> None:
        self.act("edu_set", {"on": on})
        self.act("teacher_only_set", {"on": on}, "授業モードをオンにしました" if on else "授業モードをオフにしました")

    def render(self, s: dict) -> None:
        on = bool(s.get("edu_on")) and bool(s.get("teacher_only", True))
        if self.mode.isChecked() != on and not self.mode.hasFocus():
            self.mode.blockSignals(True)
            self.mode.setChecked(on)
            self.mode.blockSignals(False)
        self.ready.setText(self._ready_html(s.get("ready")))
        items = s.get("lesson") or []
        devs = [x for x in items if x.get("online")] or items
        struct = json.dumps([(x["id"], x["name"]) for x in devs], ensure_ascii=False)
        if struct != self.key:                                   # 台数・名前が変わったときだけ、カードを作り直す
            self.key = struct
            self.cards = {}
            while self.hl.count():
                it = self.hl.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            for x in devs:
                c = DeviceCard(x["id"], self.act)
                self.hl.addWidget(c, 1)
                self.cards[x["id"]] = c
            if not devs:
                self.hl.addWidget(QLabel("接続中の機器がありません"))
        for x in devs:
            c = self.cards.get(x["id"])
            if c is not None:
                c.render(x)
        self.stop_all.setEnabled(any(x.get("on") for x in items))

    @staticmethod
    def _ready_html(rd: list) -> str:
        """準備チェックの要約: 直したほうがよいものだけ、1 行で。"""
        if not isinstance(rd, list) or not rd:
            return ""
        bad = [r for r in rd if r.get("ok") is False]
        if not bad:
            return f'<span style="color:{GR}">✅ 準備チェック: だいじょうぶです</span>'
        return f'<span style="color:{AM}">⚠ 準備チェック: ' + " / ".join(esc(f'{r.get("label", "")} {r.get("note", "")}'.strip()) for r in bad[:3]) + "</span>"
