#!/usr/bin/env python3
"""StackChan 管理アプリ(Ubuntu のデスクトップアプリ)。ブラウザは使わず、Qt の部品で作っている。

ブリッジの管理用 HTTP(既定 http://127.0.0.1:8080)へつなぎ、状態の表示と操作をする。
  SC_ADMIN_URL    つなぎ先(別のマシンから使うとき)
  SC_ADMIN_TOKEN  合言葉(ブリッジ側で設定したとき)
実行:  python stackchan_admin.py [--offscreen-shot out.png]
"""
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QInputDialog, QGraphicsDropShadowEffect, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSlider, QTabWidget, QVBoxLayout, QWidget)

BASE = os.environ.get("SC_ADMIN_URL", "http://127.0.0.1:8080").rstrip("/")
TOKEN = os.environ.get("SC_ADMIN_TOKEN", "")
NAMES = {"en": "EN", "zh": "ZH", "ko": "KO", "ja": "JA"}

CY, MG, GR, AM, RD = "#3fe0ff", "#ff4fd8", "#41ffa8", "#ffc14d", "#ff5a6e"
MONO = '"JetBrains Mono","DejaVu Sans Mono","Noto Sans Mono CJK JP","Noto Sans CJK JP",monospace'

QSS = f"""
QWidget{{color:#d9ecff;font-size:13px;font-family:{MONO}}}
QMainWindow{{background:#04070d}}
QTabWidget::pane{{border:0;top:6px;background:transparent}}
QScrollArea,#page{{background:transparent;border:0}}
QTabBar::tab{{background:transparent;padding:9px 22px;color:#6f8aa6;border:0;border-bottom:2px solid rgba(63,224,255,40);margin-right:6px;font-size:13px;letter-spacing:2px}}
QTabBar::tab:selected{{color:{CY};border-bottom:2px solid {CY};background:rgba(63,224,255,22)}}
QTabBar::tab:hover{{color:#d9ecff}}
QFrame#card{{background:rgba(14,26,44,190);border:1px solid rgba(63,224,255,70);border-radius:4px}}
QFrame#node{{background:rgba(5,12,22,170);border:1px solid rgba(63,224,255,60);border-radius:4px}}
QFrame#node[st="active"]{{border:1px solid {CY};background:rgba(63,224,255,40)}}
QFrame#node[st="done"]{{border:1px solid rgba(65,255,168,170)}}
QLabel{{background:transparent}}
QLabel#h{{font-weight:700;font-size:12px;color:{CY}}}
QLabel#mute{{color:#6f8aa6;font-size:12px}}
QLabel#big{{font-size:26px;font-weight:700;color:{CY}}}
QFrame#kvbox{{background:rgba(2,8,16,150);border:1px solid rgba(63,224,255,55);border-left:3px solid {CY};border-radius:3px}}
QPushButton{{background:rgba(63,224,255,18);border:1px solid rgba(63,224,255,120);border-radius:3px;padding:8px 14px;color:#d9ecff}}
QPushButton:hover{{background:rgba(63,224,255,55);border:1px solid {CY};color:#ffffff}}
QPushButton:pressed{{background:rgba(63,224,255,95)}}
QPushButton:checked{{border:1px solid {GR};color:{GR};background:rgba(65,255,168,34)}}
QPushButton#pri{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {CY},stop:1 {MG});color:#04070d;font-weight:700;border:0}}
QPushButton#pri:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7aeaff,stop:1 #ff86e6)}}
QPushButton#bad{{border:1px solid {RD};color:{RD};background:rgba(255,90,110,18)}}
QPushButton#bad:hover{{background:rgba(255,90,110,60)}}
QLineEdit,QComboBox{{background:rgba(2,8,16,190);border:1px solid rgba(63,224,255,90);border-radius:3px;padding:8px 10px;selection-background-color:{MG}}}
QLineEdit:focus,QComboBox:focus{{border:1px solid {CY}}}
QComboBox QAbstractItemView{{background:#08111d;selection-background-color:rgba(63,224,255,80);border:1px solid {CY}}}
QSlider::groove:horizontal{{height:6px;border-radius:3px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {CY},stop:1 {MG})}}
QSlider::handle:horizontal{{background:#04070d;border:2px solid {CY};width:16px;height:16px;margin:-7px 0;border-radius:9px}}
QSlider::handle:horizontal:hover{{border:2px solid #ffffff}}
QListWidget{{background:transparent;border:0;outline:0}}
QListWidget::item{{border-bottom:1px dashed rgba(63,224,255,45);padding:7px 4px;color:#9fd8ee}}
QProgressBar{{background:rgba(255,255,255,18);border:0;border-radius:2px;max-height:4px}}
QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {CY},stop:1 {MG});border-radius:2px}}
QScrollBar:vertical{{background:transparent;width:8px}}
QScrollBar::handle:vertical{{background:rgba(63,224,255,110);border-radius:4px;min-height:30px}}
QScrollBar::add-line,QScrollBar::sub-line{{height:0}}
QToolTip{{background:#08111d;color:#d9ecff;border:1px solid {CY}}}
"""


# ---------------------------------------------------------------- 通信
class Api(QObject):
    def __init__(self):
        super().__init__()
        self.nam = QNetworkAccessManager(self)

    def _req(self, path: str) -> QNetworkRequest:
        r = QNetworkRequest(QUrl(BASE + path + (("&" if "?" in path else "?") + "token=" + TOKEN if TOKEN else "")))
        r.setTransferTimeout(4000)
        return r

    def get(self, path: str, cb) -> None:
        rep = self.nam.get(self._req(path))
        rep.finished.connect(lambda: self._done(rep, cb))

    def post(self, name: str, body: dict | None = None, cb=None) -> None:
        r = self._req("/api/" + name)
        rep = self.nam.post(r, json.dumps(body or {}).encode())
        rep.finished.connect(lambda: self._done(rep, cb or (lambda ok, data: None)))

    @staticmethod
    def _done(rep, cb) -> None:
        ok = rep.error().value == 0
        data = bytes(rep.readAll()) if ok else b""
        rep.deleteLater()
        cb(ok, data)


# ---------------------------------------------------------------- ロボットの顔
class Face(QWidget):
    COL = {"off": RD, "idle": CY, "listen": GR, "think": AM, "speak": MG}

    def __init__(self):
        super().__init__()
        self.mood, self.t = "off", 0
        self.setFixedSize(84, 84)
        tm = QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(70)

    def _tick(self):
        self.t += 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.width() / 100, self.height() / 100)
        col = QColor(self.COL.get(self.mood, CY))
        glow = QColor(col)
        glow.setAlpha(45)
        for wd in (9, 6):                                         # ネオンのにじみ
            p.setPen(QPen(glow, wd))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(12, 16, 76, 68), 20, 20)
        p.setPen(QPen(col, 2.5))
        p.drawLine(50, 6, 50, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(MG))
        p.drawEllipse(QPointF(50, 5), 3.5, 3.5)
        p.setPen(QPen(col, 2.5))
        p.setBrush(QColor("#08111d"))
        p.drawRoundedRect(QRectF(12, 16, 76, 68), 20, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(col if self.mood != "off" else QColor(255, 90, 110, 130))
        h, dx = 20.0, 0.0
        if self.mood == "idle" and self.t % 100 < 3:
            h = 3.0
        elif self.mood == "listen":
            h = 24.0
        elif self.mood == "think":
            dx = 5 * math.sin(self.t * 0.28)
        for x in (27, 59):
            g2 = QColor(col)
            g2.setAlpha(60)
            p.setBrush(g2)
            p.drawRoundedRect(QRectF(x + dx - 3, 48 - h / 2 - 3, 20, h + 6), 8, 8)
            p.setBrush(col if self.mood != "off" else QColor(255, 90, 110, 130))
            p.drawRoundedRect(QRectF(x + dx, 48 - h / 2, 14, h), 6, 6)
        mh = 5 + (8 * abs(math.sin(self.t * 0.5)) if self.mood == "speak" else 0)
        p.drawRoundedRect(QRectF(38, 70.5 - mh / 2, 24, mh), 2.5, 2.5)


# ---------------------------------------------------------------- 部品
class Card(QFrame):
    """角にブラケット(「」型)の飾りを付けたパネル。"""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, L = self.width() - 1, self.height() - 1, 14
        p.setPen(QPen(QColor(CY), 2))
        p.drawPolyline([QPointF(0, L), QPointF(0, 0), QPointF(L, 0)])
        p.drawPolyline([QPointF(w - L, h), QPointF(w, h), QPointF(w, h - L)])
        p.setPen(QPen(QColor(MG), 2))
        p.drawPolyline([QPointF(w - L, 0), QPointF(w, 0), QPointF(w, L)])
        p.drawPolyline([QPointF(0, h - L), QPointF(0, h), QPointF(L, h)])


EN = {"パイプライン": "PIPELINE", "使っているモデル": "MODELS IN USE", "状態": "TELEMETRY", "サーバー": "SERVER", "動きの履歴": "EVENT STREAM",
      "音量": "VOLUME", "翻訳の置き場所": "ENGINE", "言語": "LANGUAGE", "声と聞き取りの調整": "VOICE TUNING", "しゃべらせる": "SPEAK",
      "首・表情・動作": "CONTROL", "機器": "DEVICES", "3人会話": "DISCUSSION", "新しい機器を追加": "ADD DEVICE", "端末の画面": "DISPLAY", "会話の記録": "CONVERSATIONS", "人": "PEOPLE"}


class MicScope(QWidget):
    """マイクの音の大きさを、波形メーターのように動かす(ヘッダーの真ん中)。"""

    N = 56

    def __init__(self):
        super().__init__()
        self.setMinimumSize(220, 54)
        self.level, self.live = 0.0, False
        self.vals = [0.0] * self.N
        self.ph = 0.0
        tm = QTimer(self)
        tm.timeout.connect(self._tick)
        tm.start(66)

    def _tick(self):
        import random
        self.ph += 0.35
        tgt = self.level if self.live else 0.0
        v = max(tgt * (0.55 + 0.45 * random.random()), 0.04 + 0.03 * abs(math.sin(self.ph)))
        self.vals = self.vals[1:] + [v]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bw = w / self.N
        for i, v in enumerate(self.vals):
            f = i / (self.N - 1)
            col = QColor(int(63 + (255 - 63) * f), int(224 + (79 - 224) * f), int(255 + (216 - 255) * f), 70 + int(160 * min(1.0, v * 2)))
            bh = max(2.0, min(1.0, v) * (h - 4))
            p.fillRect(QRectF(i * bw + 1, (h - bh) / 2, max(1.0, bw - 2), bh), col)
        p.setPen(QPen(QColor(63, 224, 255, 60), 1))
        p.drawLine(0, int(h / 2), w, int(h / 2))


class Link(QWidget):
    """パイプラインの段と段のあいだ。流れているときは光の粒が走る。"""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(26)
        self.flow = False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, y = self.width(), self.height() / 2
        p.setPen(QPen(QColor(63, 224, 255, 150 if self.flow else 60), 2))
        p.drawLine(2, int(y), w - 8, int(y))
        p.drawLine(w - 8, int(y) - 4, w - 3, int(y))
        p.drawLine(w - 8, int(y) + 4, w - 3, int(y))
        if self.flow:
            x = 2 + ((time.time() * 70) % (w - 8))
            g = QRadialGradient(x, y, 7)
            g.setColorAt(0, QColor(255, 255, 255, 240))
            g.setColorAt(0.35, QColor(63, 224, 255, 200))
            g.setColorAt(1, QColor(255, 79, 216, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(x, y), 7, 7)


class Gauge(QWidget):
    """円形のメーター。value は 0〜100。bad_high=True なら高いほど危険(負荷・温度)。"""

    def __init__(self, caption: str, bad_high: bool = True):
        super().__init__()
        self.caption, self.bad_high = caption, bad_high
        self.value, self.text = 0.0, "–"
        self.setMinimumSize(96, 104)

    def set(self, value: float, text: str):
        self.value, self.text = max(0.0, min(100.0, value)), text
        self.update()

    def color(self) -> QColor:
        v = self.value if self.bad_high else 100 - self.value
        return QColor(CY if v < 60 else AM if v < 82 else RD)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = min(self.width(), self.height() - 18) - 14
        r = QRectF((self.width() - d) / 2, 6, d, d)
        p.setPen(QPen(QColor(63, 224, 255, 34), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 225 * 16, -270 * 16)
        col = self.color()
        glow = QColor(col)
        glow.setAlpha(50)
        span = int(-270 * 16 * self.value / 100)
        p.setPen(QPen(glow, 12, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 225 * 16, span)
        p.setPen(QPen(col, 6, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 225 * 16, span)
        p.setPen(QColor("#ffffff"))
        f = p.font()
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, Qt.AlignCenter, self.text)
        p.setPen(QColor("#6f8aa6"))
        f.setPointSize(8)
        f.setBold(False)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.drawText(QRectF(0, d + 6, self.width(), 16), Qt.AlignCenter, self.caption)


class Spark(QWidget):
    """「最初の音まで」の推移を、ネオンの折れ線で描く。"""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(58)
        self.a: list = []

    def set(self, a):
        self.a = list(a or [])
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(63, 224, 255, 28), 1))
        for k in range(1, 4):
            p.drawLine(0, int(h * k / 4), w, int(h * k / 4))
        if len(self.a) < 2:
            return
        mx = max(2.0, max(self.a))
        st = w / (len(self.a) - 1)
        pts = [QPointF(i * st, h - 6 - (v / mx) * (h - 14)) for i, v in enumerate(self.a)]
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        fill = QPainterPath(path)
        fill.lineTo(QPointF(w, h))
        fill.lineTo(QPointF(0, h))
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(63, 224, 255, 70))
        g.setColorAt(1, QColor(63, 224, 255, 0))
        p.fillPath(fill, QBrush(g))
        lg = QLinearGradient(0, 0, w, 0)
        lg.setColorAt(0, QColor(CY))
        lg.setColorAt(1, QColor(MG))
        p.setPen(QPen(QBrush(lg), 2))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(pts[-1], 3.2, 3.2)


class Root(QWidget):
    """サイバー風の背景: 暗い青 + 光のにじみ + 細かいグリッド。"""

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if getattr(self, "_bg", None) is None or self._bg.size() != self.size():     # 背景は大きさが変わったときだけ描く(毎フレーム描くと重くてチカチカする)
            self._bg = QPixmap(self.size())
            self._paint_bg(QPainter(self._bg), w, h)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)

    def _paint_bg(self, p, w, h):
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor("#0a111d"))
        g.setColorAt(1, QColor("#04070d"))
        p.fillRect(self.rect(), g)
        for cx, cy, r, col in ((w * 0.88, -40, 760, QColor(63, 224, 255, 38)), (-60, h * 0.25, 640, QColor(255, 79, 216, 26))):
            rg = QRadialGradient(cx, cy, r)
            rg.setColorAt(0, col)
            rg.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), QBrush(rg))
        p.setPen(QPen(QColor(63, 224, 255, 12), 1))
        for x in range(0, w, 44):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 44):
            p.drawLine(0, y, w, y)
        p.end()


def card(title: str = "", sub: str = "") -> tuple[QFrame, QVBoxLayout]:
    f = Card()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if title:
        row = QHBoxLayout()
        h = QLabel(f'▍{EN.get(title, title.upper())}  <span style="color:#6f8aa6;font-weight:400">{title}</span>' if title in EN else "▍" + title)
        h.setTextFormat(Qt.RichText)
        h.setObjectName("h")
        fnt = h.font()
        fnt.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        h.setFont(fnt)
        row.addWidget(h)
        row.addStretch()
        if sub:
            m = QLabel(sub)
            m.setObjectName("mute")
            row.addWidget(m)
        lay.addLayout(row)
    return f, lay


def btn(text: str, fn=None, checkable=False, name="") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checkable)
    if name:
        b.setObjectName(name)
    if fn:
        b.clicked.connect(lambda _=False: fn())
    return b


def muted(text: str = "") -> QLabel:
    l = QLabel(text)
    l.setObjectName("mute")
    l.setWordWrap(True)
    return l


def hrow(*ws, stretch=False) -> QHBoxLayout:
    r = QHBoxLayout()
    r.setSpacing(8)
    for w in ws:
        r.addWidget(w)
    if stretch:
        r.addStretch()
    return r


def place(w: str) -> str:
    w = (w or "").replace("このMac", "サーバー CPU").replace("このサーバーCPU", "サーバー CPU")
    return w.replace("このサーバーのCPU", "サーバー CPU").replace("このサーバー", "サーバー CPU").replace("CPU CPU", "CPU")


def where_color(w: str) -> str:
    return GR if ("GPU" in w or "s109" in w) else (AM if ("CPU" in w or "サーバー" in w) else CY)


class KV(QFrame):
    """小さな見出し + 値。2つのラベルを縦に並べる(1つの文字ブロックにすると環境によって行が重なるため)。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("kvbox")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self.cap, self.val = QLabel(), QLabel()
        self.cap.setStyleSheet("color:#9aa6bd;font-size:11px")
        self.val.setStyleSheet("font-size:17px;font-weight:600")
        self.val.setWordWrap(False)
        lay.addWidget(self.cap)
        lay.addWidget(self.val)
        self.setMinimumHeight(66)
        self.setMinimumWidth(110)

    def set(self, cap: str, val: str):
        self.cap.setText(cap)
        self.val.setText(val)


class Node(QFrame):
    def paintEvent(self, e):
        super().paintEvent(e)
        if self._st == "active":                                   # 動いている段は、光が上を流れる
            p = QPainter(self)
            w = self.width()
            x = (time.time() * 260) % (w + 120) - 120
            g = QLinearGradient(x, 0, x + 120, 0)
            g.setColorAt(0, QColor(63, 224, 255, 0))
            g.setColorAt(0.5, QColor(63, 224, 255, 230))
            g.setColorAt(1, QColor(255, 79, 216, 0))
            p.fillRect(QRectF(max(0, x), 0, min(120, w - max(0, x)), 3), QBrush(g))

    def __init__(self, key: str, title: str):
        super().__init__()
        self._st = ""
        self.setObjectName("node")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(3)
        k = QLabel(key)
        k.setObjectName("mute")
        t = QLabel(title)
        t.setObjectName("h")
        self.model, self.where, self.detail = QLabel(), QLabel(), muted()
        self.model.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        for w in (k, t, self.model, self.where, self.bar, self.detail):
            lay.addWidget(w)
        lay.addStretch()
        self.setMinimumHeight(150)

    def set(self, st="", model="", where="", detail="", pct=0.0):
        if st != self._st:
            self._st = st
            if st == "active":
                fx = QGraphicsDropShadowEffect(self)
                fx.setColor(QColor(63, 224, 255, 200))
                fx.setBlurRadius(26)
                fx.setOffset(0, 0)
                self.setGraphicsEffect(fx)
            else:
                self.setGraphicsEffect(None)
            self.setProperty("st", st)
            self.style().unpolish(self)
            self.style().polish(self)
        self.model.setText(model or "")
        self.where.setText(f'<span style="color:{where_color(where)}">● {where}</span>' if where else "")
        self.detail.setText(detail or "")
        self.bar.setValue(int(max(0.0, min(1.0, pct)) * 100))


class Slider(QWidget):
    """動かしている間は画面の更新で動かさない。離したときと、動かしている途中(間引き)で送る。"""

    def __init__(self, lo, hi, step, fmt, send):
        super().__init__()
        self.lo, self.step, self.fmt, self.send = lo, step, fmt, send
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, int(round((hi - lo) / step)))
        self.val = QLabel()
        self.val.setObjectName("big")
        self.val.setStyleSheet(f"font-size:14px;color:{CY};font-weight:600;min-width:64px")
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.val)
        self._last = 0.0
        self.s.valueChanged.connect(self._moved)
        self.s.sliderReleased.connect(lambda: self.send(self.value()))
        self._prog = False

    def value(self) -> float:
        return self.lo + self.s.value() * self.step

    def _moved(self, _):
        self.val.setText(self.fmt(self.value()))
        if self._prog:
            return
        if self.s.isSliderDown():
            if time.time() - self._last > 0.25:
                self._last = time.time()
                self.send(self.value())
        else:                                  # キー操作・ホイール
            self.send(self.value())

    def set_value(self, v: float):
        if self.s.isSliderDown():
            return
        self._prog = True
        self.s.setValue(int(round((v - self.lo) / self.step)))
        self.val.setText(self.fmt(v))
        self._prog = False


# ---------------------------------------------------------------- 本体
class Win(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api = Api()
        self.cur: dict = {}
        self.busy = False
        self.fails = 0
        self.build0 = None
        self.turn_key = ""
        self.ph_key = ""
        self.edit_ph = False
        self.setWindowTitle("StackChan")
        self.resize(1180, 860)

        root = Root()
        root.setObjectName("root")
        self.setCentralWidget(root)
        top = QVBoxLayout(root)
        top.setContentsMargins(18, 14, 18, 14)
        top.setSpacing(6)

        # ---- ヘッダー
        self.face = Face()
        self.title = QLabel("STACKCHAN  CORE")
        tf = self.title.font()
        tf.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        tf.setPointSize(17)
        tf.setBold(True)
        self.title.setFont(tf)
        self.title.setStyleSheet(f"color:{CY}")
        gl = QGraphicsDropShadowEffect(self.title)
        gl.setColor(QColor(63, 224, 255, 190))
        gl.setBlurRadius(22)
        gl.setOffset(0, 0)
        self.title.setGraphicsEffect(gl)
        self.sub = muted("// LINK OFFLINE")
        sf = self.sub.font()
        sf.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self.sub.setFont(sf)
        tb = QVBoxLayout()
        tb.addStretch()
        tb.addWidget(self.title)
        tb.addWidget(self.sub)
        tb.addStretch()
        self.led = QLabel("● OFFLINE")
        self.clock = muted()
        self.clock.setAlignment(Qt.AlignRight)
        self.led.setAlignment(Qt.AlignRight)
        rb = QVBoxLayout()
        rb.addStretch()
        rb.addWidget(self.led)
        rb.addWidget(self.clock)
        rb.addStretch()
        fs = btn("全画面", self.toggle_fs)
        head = QHBoxLayout()
        head.addWidget(self.face)
        head.addSpacing(8)
        head.addLayout(tb)
        head.addSpacing(20)
        self.scope = MicScope()
        head.addWidget(self.scope, 1)
        head.addSpacing(20)
        head.addWidget(fs)
        head.addSpacing(12)
        head.addLayout(rb)
        top.addLayout(head)

        self.chip_w = QWidget()
        self.chip_l = QHBoxLayout(self.chip_w)
        self.chip_l.setContentsMargins(0, 2, 0, 2)
        self.chip_l.setSpacing(8)
        self.chip_key = ""
        top.addWidget(self.chip_w)

        self.tabs = QTabWidget()
        top.addWidget(self.tabs, 1)
        self.tabs.addTab(self.page_status(), "ステータス")
        self.tabs.addTab(self.page_ctrl(), "操作")
        self.tabs.addTab(self.page_devices(), "機器")
        self.tabs.addTab(self.page_discuss(), "3人会話")
        self.tabs.addTab(self.page_talk(), "会話")

        self.msg = muted()
        top.addWidget(self.msg)

        self.sweep = QTimer(self)                                  # 動いている段の光を流す
        self.sweep.timeout.connect(self._sweep)
        self.sweep.start(70)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.poll()

    def _sweep(self):
        order = list(self.nodes.values())
        for i, lk in enumerate(self.links):
            a, b = order[i], order[i + 1]
            lk.flow = a._st in ("active", "done") or b._st == "active"
            if lk.flow:
                lk.update()
        for n in order:
            if n._st == "active":
                n.update()

    def toggle_fs(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F11:
            self.toggle_fs()
        elif e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(e)

    @staticmethod
    def scroll(inner: QWidget) -> QScrollArea:
        inner.setObjectName("page")
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.viewport().setAutoFillBackground(False)
        sa.setFrameShape(QFrame.NoFrame)
        return sa

    def act(self, name, body=None, ok_msg=""):
        def cb(ok, data):
            try:
                j = json.loads(data) if ok else {"ok": False, "error": "通信できません"}
            except ValueError:
                j = {"ok": False, "error": "応答が読めません"}
            self.msg.setText(ok_msg if j.get("ok") and ok_msg else ("" if j.get("ok") else "⚠ " + str(j.get("error", "失敗"))))
        self.api.post(name, body, cb)

    # ---------------- ページ: ステータス
    def page_status(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        pc, pl = card("パイプライン", "声が入ってから、読み上げまでの流れ")
        row = QHBoxLayout()
        row.setSpacing(0)
        self.nodes = {}
        self.links: list = []
        for key, k, t in (("mic", "01 MIC", "マイク"), ("vad", "02 VAD", "声の検出"), ("stt", "03 STT", "聞き取り"),
                          ("translate", "04 LLM", "翻訳"), ("tts", "05 TTS", "声づくり"), ("play", "06 OUT", "再生")):
            if self.nodes:
                lk = Link()
                self.links.append(lk)
                row.addWidget(lk)
            n = Node(k, t)
            self.nodes[key] = n
            row.addWidget(n, 1)
        pl.addLayout(row)
        self.summary = muted()
        pl.addWidget(self.summary)
        lay.addWidget(pc)

        mc, ml = card("使っているモデル", "いま動いているもの")
        self.models = QLabel()
        self.models.setTextFormat(Qt.RichText)
        self.models.setWordWrap(True)
        self.models.setMinimumHeight(130)
        ml.addWidget(self.models)
        lay.addWidget(mc)

        grid = QHBoxLayout()
        grid.setSpacing(12)
        tc, tl = card("状態")
        gr = QHBoxLayout()
        self.g_bat, self.g_wifi = Gauge("BATTERY", bad_high=False), Gauge("Wi-Fi", bad_high=False)
        gr.addWidget(self.g_bat)
        gr.addWidget(self.g_wifi)
        tl.addLayout(gr)
        self.tele = QGridLayout()
        self.tele.setSpacing(8)
        tl.addLayout(self.tele)
        self.tele_w: dict[str, KV] = {}
        tl.addStretch()
        grid.addWidget(tc, 1)
        sc, sl = card("サーバー")
        gr = QHBoxLayout()
        self.g_load, self.g_mem, self.g_temp = Gauge("LOAD"), Gauge("MEMORY"), Gauge("TEMP")
        for g in (self.g_load, self.g_mem, self.g_temp):
            gr.addWidget(g)
        sl.addLayout(gr)
        self.sysg = QGridLayout()
        self.sysg.setSpacing(8)
        sl.addLayout(self.sysg)
        self.sys_w: dict[str, KV] = {}
        self.svc_row = QHBoxLayout()
        sl.addLayout(self.svc_row)
        self.svc_b: dict[str, QPushButton] = {}
        sl.addStretch()
        grid.addWidget(sc, 1)
        lay.addLayout(grid)

        ec, el = card("動きの履歴")
        self.events = QListWidget()
        self.events.setMinimumHeight(160)
        el.addWidget(self.events)
        lay.addWidget(ec)
        lay.addStretch()
        return self.scroll(w)

    def kv(self, grid: QGridLayout, store: dict, key: str, label: str, value: str, idx: int):
        w = store.get(key)
        if w is None:
            w = KV()
            store[key] = w
            grid.addWidget(w, idx // 3, idx % 3)
        w.set(label, value)

    # ---------------- ページ: 操作
    def page_ctrl(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        L, R = QVBoxLayout(), QVBoxLayout()
        L.setSpacing(12)
        R.setSpacing(12)
        outer.addLayout(L, 1)
        outer.addLayout(R, 1)

        # 音量
        c, l = card("音量")
        self.vol_num = QLabel("–")
        self.vol_num.setObjectName("big")
        self.vol = Slider(20, 255, 1, lambda v: f"{round(v * 100 / 255)}%", lambda v: self.act("volume", {"v": int(v)}))
        l.addLayout(hrow(btn("−", lambda: self.act("volume", {"delta": -20})), self.vol, btn("＋", lambda: self.act("volume", {"delta": 20}))))
        l.addLayout(hrow(*[btn(t, lambda v=v: self.act("volume", {"v": v})) for t, v in
                           (("MIN", 20), ("LOW", 90), ("MID", 150), ("HIGH", 210), ("MAX", 255))], stretch=True))
        L.addWidget(c)

        # 置き場所
        c, l = card("翻訳の置き場所")
        self.eng_b = {}
        row = QHBoxLayout()
        for m, t in (("auto", "AUTO s109→CPU"), ("s109", "s109 GPU"), ("local", "サーバー CPU")):
            b = btn(t, lambda m=m: self.act("engine", {"mode": m}), checkable=True)
            self.eng_b[m] = b
            row.addWidget(b)
        row.addStretch()
        l.addLayout(row)
        self.lat_lbl = muted()
        l.addWidget(self.lat_lbl)
        self.spark = Spark()
        l.addWidget(self.spark)
        L.addWidget(c)

        # 言語
        c, l = card("言語", "通訳する言語(日本語 ↔ )")
        self.lang_b = {}
        row = QHBoxLayout()
        for code, t in (("en", "English"), ("zh", "中文"), ("ko", "한국어")):
            b = btn(t, lambda code=code: self.act("lang", {"code": code}), checkable=True)
            self.lang_b[code] = b
            row.addWidget(b)
        row.addStretch()
        l.addLayout(row)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("話題 例: 医療 / 会議 / 旅行(空で解除)")
        self.topic.returnPressed.connect(lambda: self.act("topic", {"text": self.topic.text()}))
        l.addLayout(hrow(self.topic, btn("設定", lambda: self.act("topic", {"text": self.topic.text()}))))
        L.addWidget(c)

        # 声の調整
        c, l = card("声と聞き取りの調整")
        l.addWidget(muted("日本語の声"))
        self.voice = QComboBox()
        self.voice.activated.connect(lambda i: self.act("voice", {"id": int(self.voice.itemData(i))}))
        l.addWidget(self.voice)
        l.addWidget(muted("話速"))
        self.speed = Slider(0.7, 1.5, 0.05, lambda v: f"{v:.2f}×", lambda v: self.act("speed", {"scale": round(v, 2)}))
        l.addWidget(self.speed)
        l.addWidget(muted("雑音ゲート(高い=雑音に強い)"))
        self.gate = Slider(1.0, 4.0, 0.1, lambda v: f"{v:.1f}×", lambda v: self.act("gate", {"mult": round(v, 1)}))
        l.addWidget(self.gate)
        l.addWidget(muted("声の検出しきい値(低い=敏感)"))
        self.vad = Slider(0.3, 0.9, 0.05, lambda v: f"{v:.2f}", lambda v: self.act("vad", {"threshold": round(v, 2)}))
        l.addWidget(self.vad)
        self.mic_lbl = muted()
        l.addWidget(self.mic_lbl)
        l.addLayout(hrow(btn("▶ 直近15秒のマイク音声を聞く", self.play_mic), stretch=True))
        L.addWidget(c)
        L.addStretch()

        # しゃべらせる
        c, l = card("しゃべらせる")
        self.say_lang = QComboBox()
        for code, t in (("ja", "日本語"), ("en", "English"), ("zh", "中文"), ("ko", "한국어")):
            self.say_lang.addItem(t, code)
        self.say = QLineEdit()
        self.say.setPlaceholderText("読み上げる文章")
        self.say.returnPressed.connect(self.do_say)
        l.addLayout(hrow(self.say_lang, self.say, btn("話す", self.do_say, name="pri")))
        l.addWidget(muted("定型フレーズ(押すとすぐ話す)"))
        self.ph_box = QHBoxLayout()
        self.ph_wrap = QWidget()
        self.ph_wrap.setLayout(self.ph_box)
        l.addWidget(self.ph_wrap)
        self.ph_edit = btn("編集", self.toggle_edit_ph, checkable=True)
        self.ph_add = btn("入力欄の文章を定型に追加", self.add_phrase)
        self.ph_add.hide()
        l.addLayout(hrow(self.ph_edit, self.ph_add, stretch=True))
        R.addWidget(c)

        # 首・表情
        c, l = card("首・表情・動作")
        pad = QGridLayout()
        pad.setSpacing(6)
        for (r, cc), (t, d) in {(0, 1): ("▲", "up"), (1, 0): ("◀", "left"), (1, 1): ("●", "center"),
                                (1, 2): ("▶", "right"), (2, 1): ("▼", "down")}.items():
            b = btn(t, lambda d=d: self.act("neck", {"dir": d}))
            b.setFixedSize(56, 44)
            pad.addWidget(b, r, cc)
        padw = QHBoxLayout()
        padw.addStretch()
        padw.addLayout(pad)
        padw.addStretch()
        l.addLayout(padw)
        l.addLayout(hrow(*[btn(t, lambda s=s: self.act("face", {"state": s})) for t, s in
                           (("NEUTRAL", "neutral"), ("HAPPY", "happy"), ("SAD", "sad"), ("LISTEN", "listening"), ("THINK", "thinking"))],
                         stretch=True))
        self.tg_track = btn("顔追従(首が顔を追う)", lambda: self.act("track", {"on": not self.cur.get("track", True)}), checkable=True)
        self.tg_listen = btn("聞き取り(オフでマイクを無視)", lambda: self.act("listen", {"on": not self.cur.get("listen", True)}), checkable=True)
        l.addLayout(hrow(self.tg_track, self.tg_listen, stretch=True))
        R.addWidget(c)

        # 人(顔の識別・手を振る)
        c, l = card("人", "誰かを見分ける・手を振る")
        self.who = QLabel("—")
        self.who.setObjectName("big")
        self.waves = muted()
        l.addWidget(muted("いま見えている人"))
        l.addWidget(self.who)
        l.addWidget(self.waves)
        self.tg_wave = btn("手を振ったら反応する", lambda: self.act("wave", {"on": not (self.cur.get("people") or {}).get("wave_on", True)}), checkable=True)
        self.tg_wvoice = btn("声でも返事する", lambda: self.act("wave", {"voice": not (self.cur.get("people") or {}).get("wave_voice", True)}), checkable=True)
        l.addLayout(hrow(self.tg_wave, self.tg_wvoice, stretch=True))
        l.addWidget(muted("顔を登録する(カメラに向かって、顔を正面に)"))
        self.regname = QLineEdit()
        self.regname.setPlaceholderText("名前 例: はま")
        self.regname.returnPressed.connect(self.do_register)
        l.addLayout(hrow(self.regname, btn("登録", self.do_register, name="pri")))
        self.regmsg = muted()
        l.addWidget(self.regmsg)
        self.known_box = QHBoxLayout()
        kw = QWidget()
        kw.setLayout(self.known_box)
        l.addWidget(kw)
        self.known_key = ""
        R.addWidget(c)

        # 画面
        c, l = card("端末の画面")
        self.bright = 140
        l.addLayout(hrow(btn("撮る", self.capture), btn("暗く", lambda: self.set_bright(-30)), btn("明るく", lambda: self.set_bright(30)),
                         btn("校正", lambda: self.act("recalibrate")), stretch=True))
        self.shot = QLabel()
        self.shot.setAlignment(Qt.AlignCenter)
        self.shot.hide()
        l.addWidget(self.shot)
        R.addWidget(c)
        R.addStretch()
        return self.scroll(w)

    # ---------------- ページ: 機器
    def page_devices(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        c, l = card("機器", "カードの「選ぶ」で、操作する機器を切り替えます")
        self.dev_box = QVBoxLayout()
        self.dev_box.setSpacing(10)
        l.addLayout(self.dev_box)
        lay.addWidget(c)
        c, l = card("新しい機器を追加")
        t = QLabel("電源を入れて、この Wi-Fi につなぐだけで、自動で登録・初期設定されます(名前は「StackChan-数字」。あとから変えられます)。<br><br>"
                   "初めて使う機器(何もしていない新品)は、Mac に USB で挿すと、待ち受け中の <b>tools/provision.py</b> が"
                   "書き込みから自動で行います。登録されると、この画面に現れます。")
        t.setTextFormat(Qt.RichText)
        t.setWordWrap(True)
        l.addWidget(t)
        lay.addWidget(c)
        lay.addStretch()
        self.dev_struct = ""
        self.dev_w = {}
        return self.scroll(w)

    def render_devices(self, s: dict):
        """機器の一覧。つくり(台数・名前・オンライン)が変わったときだけ作り直し、電池や電波などは文字だけ差し替える
        (毎回作り直すと、ボタンが消えては現れてチカチカする)。"""
        ds, tg = s.get("devices") or [], s.get("target", "all")
        struct = json.dumps([[d["id"], d["name"], d["online"], bool(d.get("auto"))] for d in ds])
        if struct != self.dev_struct:
            self.dev_struct = struct
            self.dev_w = {}
            while self.chip_l.count():
                it = self.chip_l.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            self.chip_all = btn("", lambda: self.act("device_select", {"id": "all"}), checkable=True)
            self.chip_l.addWidget(self.chip_all)
            for d in ds:
                b = btn("", lambda i=d["id"]: self.act("device_select", {"id": i}), checkable=True)
                self.chip_l.addWidget(b)
                self.dev_w[d["id"]] = {"chip": b}
            self.chip_l.addStretch()
            while self.dev_box.count():
                it = self.dev_box.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            for d in ds:
                f = QFrame()
                f.setObjectName("node")
                f.setProperty("st", "done" if d["online"] else "")
                row = QHBoxLayout(f)
                row.setContentsMargins(14, 10, 14, 10)
                lab = QLabel()
                lab.setTextFormat(Qt.RichText)
                row.addWidget(lab, 1)
                row.addWidget(btn("選ぶ", lambda i=d["id"]: self.act("device_select", {"id": i})))
                row.addWidget(btn("名前", lambda i=d["id"], n=d["name"]: self.rename_dev(i, n)))
                if d["online"]:
                    row.addWidget(btn("初期設定", lambda i=d["id"]: self.setup_dev(i)))
                else:
                    row.addWidget(btn("削除", lambda i=d["id"], n=d["name"]: self.forget_dev(i, n), name="bad"))
                self.dev_box.addWidget(f)
                self.dev_w[d["id"]]["lab"] = lab
            if not ds:
                self.dev_box.addWidget(muted("まだ機器がありません"))
        # 文字と選択状態だけ更新(変わったときだけ)
        on = sum(1 for d in ds if d["online"])
        self._set(self.chip_all, f"全機器  {on}/{len(ds)}")
        self._chk(self.chip_all, tg == "all")
        for d in ds:
            w = self.dev_w.get(d["id"])
            if not w:
                continue
            pct = (d.get("batt") or {}).get("pct")
            self._set(w["chip"], ("● " if d["online"] else "○ ") + d["name"] + (f"  {pct}%" if pct is not None and d["online"] else ""))
            self._chk(w["chip"], tg == d["id"])
            ag = d.get("seen_age")
            ago = "-" if ag is None else (f"{ag}秒前" if ag < 60 else f"{ag // 60}分前" if ag < 3600 else f"{ag // 3600}時間前")
            if d["online"]:
                pr = d.get("pair") or []
                info = (f'電池 {(d.get("batt") or {}).get("pct", "-")}%  ·  Wi-Fi {d.get("rssi", "-")} dBm  ·  音量 '
                        f'{round(d["vol"] * 100 / 255) if d.get("vol") is not None else "-"}%  ·  言語 JA↔{(pr[1] if len(pr) > 1 else d.get("lang") or "").upper()}'
                        + (f'  ·  {d["who"]}' if d.get("who") else "") + ("  ·  話し中" if d.get("talking") else ""))
            else:
                info = f"オフライン  ·  最後 {ago}"
            html = (f'<span style="color:{GR if d["online"] else "#6f8aa6"}">●</span> <b style="font-size:15px">{d["name"]}</b>'
                    + (f' <span style="color:{GR}">[自動登録]</span>' if d.get("auto") else "")
                    + f'<br><span style="color:#6f8aa6">{info}<br>{d.get("ip", "")}   fw {d.get("fw", "-")}</span>')
            self._set(w["lab"], html)

    @staticmethod
    def _set(w, text: str):
        if w.text() != text:
            w.setText(text)

    @staticmethod
    def _chk(b, on: bool):
        if b.isChecked() != on:
            b.setChecked(on)

    def rename_dev(self, dev_id: str, cur: str):
        n, ok = QInputDialog.getText(self, "名前を変える", "新しい名前", text=cur)
        if ok and n.strip():
            self.act("device_rename", {"id": dev_id, "name": n.strip()})
            self.dev_struct = ""

    def setup_dev(self, dev_id: str):
        if QMessageBox.question(self, "確認", "この機器の初期設定(音量・明るさ・向き)をやり直しますか?") == QMessageBox.Yes:
            self.act("device_setup", {"device": dev_id}, "初期設定を送りました")

    def forget_dev(self, dev_id: str, name: str):
        if QMessageBox.question(self, "確認", f"{name} を台帳から消しますか?") == QMessageBox.Yes:
            self.act("device_forget", {"id": dev_id})
            self.dev_struct = ""

    # ---------------- ページ: 3人会話
    def page_discuss(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        c, l = card("3人会話", "議題を入れると、接続中の機器が順番に話し合います(話している間はマイクを止めます)")
        self.d_topic = QLineEdit()
        self.d_topic.setPlaceholderText("議題(例: 週末は家で過ごすか、出かけるか)")
        self.d_topic.returnPressed.connect(self.discuss_go)
        self.d_turns = QComboBox()
        for n in (8, 12, 20):
            self.d_turns.addItem(f"{n}回", n)
        self.d_turns.setCurrentIndex(1)
        self.d_go = btn("▶ はじめる", self.discuss_go)
        l.addLayout(hrow(self.d_topic, self.d_turns, self.d_go, btn("■ 止める", lambda: self.act("discuss_stop"), name="bad")))
        self.d_me = QLineEdit()
        self.d_me.setPlaceholderText("会話に加わる(「私」の発言)")
        self.d_me.returnPressed.connect(self.discuss_me)
        l.addLayout(hrow(self.d_me, btn("送る", self.discuss_me)))
        self.d_stat = muted("待機中")
        l.addWidget(self.d_stat)
        self.d_log = QListWidget()
        self.d_log.setWordWrap(True)
        l.addWidget(self.d_log, 1)
        lay.addWidget(c)
        c2, l2 = card("学んだこと", "会話のたびに、機器ごとのキャラが学びを覚えて成長します")
        self.d_grow = QListWidget()
        self.d_grow.setWordWrap(True)
        l2.addWidget(self.d_grow, 1)
        l2.addLayout(hrow(btn("↺ 選んだ機器をリセット", lambda: self.forget_growth(False), name="bad"),
                          btn("↺ 全員をリセット", lambda: self.forget_growth(True), name="bad")))
        self.d_gnames: list = []
        lay.addWidget(c2, 1)
        self.d_key = ""
        return w

    def discuss_go(self):
        t = self.d_topic.text().strip()
        if t:
            self.act("discuss_start", {"topic": t, "turns": self.d_turns.currentData()}, "会話を始めます")

    def forget_growth(self, everyone: bool):
        row = self.d_grow.currentRow()
        if not everyone and not (0 <= row < len(self.d_gnames)):
            self.msg.setText("リセットする機器を、一覧から選んでください")
            return
        who = "" if everyone else self.d_gnames[row][0]
        label = "全員" if everyone else self.d_gnames[row][1]
        if QMessageBox.question(self, "確認", f"{label} の学んだことをすべて消しますか?") == QMessageBox.Yes:
            self.act("discuss_forget", {"name": who}, "リセットしました")
            self.d_key = ""

    def discuss_me(self):
        t = self.d_me.text().strip()
        if t:
            self.act("discuss_say", {"text": t})
            self.d_me.clear()

    def render_discuss(self, d):
        if not d:
            return
        k = json.dumps(d, ensure_ascii=False)
        if k == self.d_key:
            return
        self.d_key = k
        if d["on"]:
            self.d_stat.setText(f'会話中: 「{d["topic"]}」 {d["turn"]}/{d["max"]}  参加: ' + "・".join(f'{p["name"]}({p["dev_name"]})' for p in d["people"]))
        else:
            self.d_stat.setText(d.get("note") or ("終了しました" if d["log"] else "待機中"))
        self.d_go.setEnabled(not d["on"])
        self.d_log.clear()
        for x in d["log"]:
            self.d_log.addItem(f'{x["who"]}: {x["text"]}')
        self.d_log.scrollToBottom()
        cur = self.d_grow.currentRow()
        self.d_grow.clear()
        self.d_gnames = [(g["name"], g["dev_name"]) for g in d.get("growth", [])]
        for g in d.get("growth", []):
            lines = [f'■ {g["dev_name"]}({g["name"]}・{g["talks"]}回の会話)'] + [f'　・{v}' for v in g["views"]] or []
            if not g["views"]:
                lines.append("　まだ学んでいません")
            lines += [f'　{o}への印象: {t}' for o, t in g["rel"].items()]
            if g["topics"]:
                lines.append("　話した議題: " + "、".join(g["topics"][:4]))
            self.d_grow.addItem("\n".join(lines))
        if 0 <= cur < self.d_grow.count():
            self.d_grow.setCurrentRow(cur)

    # ---------------- ページ: 会話
    def page_talk(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        c, l = card("会話の記録")
        self.stats = muted()
        l.addWidget(self.stats)
        self.search = QLineEdit()
        self.search.setPlaceholderText("検索(原文・訳文)")
        self.search.textChanged.connect(lambda _: self.render_turns(force=True))
        l.addLayout(hrow(self.search, btn("CSV保存", self.save_csv), btn("画面の履歴を消す", self.clear_turns, name="bad")))
        self.turns = QListWidget()
        self.turns.setWordWrap(True)
        l.addWidget(self.turns, 1)
        lay.addWidget(c)
        return w

    # ---------------- 操作
    def do_say(self):
        t = self.say.text().strip()
        if t:
            self.act("say", {"text": t, "lang": self.say_lang.currentData()}, f"送信: {t}")

    def toggle_edit_ph(self):
        self.edit_ph = self.ph_edit.isChecked()
        self.ph_edit.setText("完了" if self.edit_ph else "編集")
        self.ph_add.setVisible(self.edit_ph)
        self.ph_key = ""
        self.render_phrases()

    def add_phrase(self):
        t = self.say.text().strip()
        if not t:
            self.msg.setText("上の入力欄に文章を入れてください")
            return
        self.act("phrases", {"list": (self.cur.get("phrases") or []) + [{"text": t, "lang": self.say_lang.currentData()}]}, "追加しました")

    def render_phrases(self):
        phs = self.cur.get("phrases") or []
        key = json.dumps(phs, ensure_ascii=False) + str(self.edit_ph)
        if key == self.ph_key:
            return
        self.ph_key = key
        while self.ph_box.count():
            it = self.ph_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, p in enumerate(phs):
            def go(i=i, p=p):
                if self.edit_ph:
                    self.act("phrases", {"list": [x for j, x in enumerate(phs) if j != i]})
                else:
                    self.act("say", {"text": p["text"], "lang": p["lang"]}, f"送信: {p['text']}")
            b = btn(("✕ " if self.edit_ph else "") + p["text"] + f"  {NAMES.get(p['lang'], '')}", go, name="bad" if self.edit_ph else "")
            self.ph_box.addWidget(b)
        self.ph_box.addStretch()

    def do_register(self):
        n = self.regname.text().strip()
        if not n:
            self.msg.setText("名前を入れてください")
            return
        self.act("face_register", {"name": n}, "カメラに顔を向けてください")

    def render_people(self, p):
        if not p:
            return
        nw = p.get("now") or {}
        self.who.setText("(識別モデルなし)" if not p.get("available") else
                         (f'{nw["name"]}  ({nw.get("score")})' if nw.get("name") else ("知らない人" if p.get("known") else "顔あり") if nw.get("face") else "顔なし"))
        age = p.get("wave_age")
        self.waves.setText(f'手を振った回数 {p.get("waves", 0)}' + (f'(最後 {int(age)}秒前)' if age is not None else ""))
        self.tg_wave.setChecked(bool(p.get("wave_on")))
        self.tg_wvoice.setChecked(bool(p.get("wave_voice")))
        r = p.get("reg")
        self.regmsg.setText(f'撮影中… {r["name"]}さん {r["got"]}/{r["need"]}' if r else (p.get("reg_msg") or ""))
        key = json.dumps(p.get("known"), ensure_ascii=False)
        if key != self.known_key:
            self.known_key = key
            while self.known_box.count():
                it = self.known_box.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            for k in p.get("known") or []:
                def rm(n=k["name"]):
                    if QMessageBox.question(self, "確認", f"{n} さんの登録を消しますか?") == QMessageBox.Yes:
                        self.act("face_delete", {"name": n})
                self.known_box.addWidget(btn(f'✕ {k["name"]}  {k["samples"]}枚', rm, name="bad"))
            self.known_box.addStretch()

    def set_bright(self, d):
        self.bright = max(15, min(255, self.bright + d))
        self.act("bright", {"v": self.bright})

    def capture(self):
        self.act("shot")
        QTimer.singleShot(2500, lambda: self.api.get("/screen.jpg", self._shot))

    def _shot(self, ok, data):
        pm = QPixmap()
        if ok and pm.loadFromData(data):
            self.shot.setPixmap(pm.scaledToWidth(min(420, pm.width()), Qt.SmoothTransformation))
            self.shot.show()

    def play_mic(self):
        def cb(ok, data):
            if not ok or len(data) < 100:
                self.msg.setText("⚠ 音声を取得できません")
                return
            f = os.path.join(tempfile.gettempdir(), "stackchan_mic.wav")
            open(f, "wb").write(data)
            for player in (["paplay"], ["pw-play"], ["aplay", "-q"], ["afplay"]):
                try:
                    subprocess.Popen(player + [f], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.msg.setText("再生中(直近15秒のマイク音声)")
                    return
                except OSError:
                    continue
            self.msg.setText("⚠ 再生できる音声プレーヤーがありません")
        self.api.get("/api/mic.wav", cb)

    def save_csv(self):
        fn, _ = QFileDialog.getSaveFileName(self, "CSV に保存", f"stackchan-{time.strftime('%Y-%m-%d')}.csv", "CSV (*.csv)")
        if not fn:
            return
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["時刻", "言語", "訳先", "原文", "訳文", "処理"])
            for t in self.cur.get("turns") or []:
                wr.writerow([t.get("ts"), t.get("from"), t.get("to"), t.get("orig"), t.get("trans"), t.get("engine")])
        self.msg.setText("保存しました: " + fn)

    def clear_turns(self):
        if QMessageBox.question(self, "確認", "画面の履歴を消しますか?(保存済みのログは消えません)") == QMessageBox.Yes:
            self.act("clear")

    def restart(self, svc: str):
        if QMessageBox.question(self, "確認", f"{svc} を再起動しますか?") == QMessageBox.Yes:
            self.act("restart", {"service": svc}, f"{svc} を再起動します")

    # ---------------- 更新
    def poll(self):
        if self.busy:
            return
        self.busy = True
        self.api.get("/api/state", self._state)

    def _state(self, ok, data):
        self.busy = False
        try:
            s = json.loads(data) if ok else None
        except ValueError:
            s = None
        if not s:
            self.fails += 1
            if self.fails >= 2:
                self.led.setText(f'<span style="color:{RD}">● NO SERVER</span>')
                self.face.mood = "off"
                self.sub.setText("// NO SIGNAL")
            return
        self.fails = 0
        b = s.get("build")
        if b:
            if self.build0 is None:
                self.build0 = b
            elif b != self.build0:                         # サーバー側が更新された(管理用の API が変わった)
                self.msg.setText("サーバーが更新されました")
                self.build0 = b
        self.cur = s
        self.apply(s)

    def apply(self, s: dict):
        conn = bool(s.get("connected"))
        self.led.setText(f'<span style="color:{GR if conn else RD}">● {"ONLINE" if conn else "OFFLINE"}</span>')
        self.clock.setText(s.get("now", ""))
        st, M = s.get("stages") or {}, s.get("models") or {}
        act = lambda i: bool(st.get(i)) and st[i].get("state") == "active"
        rec = lambda i: bool(st.get(i)) and st[i].get("state") == "done" and st[i].get("age", 9) < 3
        pl = st.get("play") or {}
        mic = s.get("mic") or {}
        N = self.nodes
        N["mic"].set("active" if conn and mic.get("live") and mic.get("level", 0) > 0.12 else ("done" if conn and mic.get("live") else ""),
                     "StackChan(端末)", "マイク", "受信中" if conn and mic.get("live") else ("無音" if conn else "未接続"), mic.get("level", 0))
        N["vad"].set("active" if s.get("vad") else "", "Silero VAD", "サーバー CPU", "声を検出中" if s.get("vad") else "待機")
        sm = M.get("stt") or {}
        stt = st.get("stt") or {}
        N["stt"].set("active" if act("stt") else ("done" if rec("stt") else ""), sm.get("name", ""), place(sm.get("where", "")),
                     f"認識中… {stt.get('sec', '')}秒の声" if act("stt") else (f"{stt.get('ms')}ms  「{stt.get('text', '')}」" if stt.get("text") else ""))
        tr = st.get("translate") or {}
        N["translate"].set("active" if act("translate") else ("done" if rec("translate") else ""), tr.get("model") or s.get("model", ""),
                           place(tr.get("where") or s.get("llm", "")),
                           f"翻訳中… {tr.get('frm', '')}→{tr.get('to', '')}" if act("translate") else (f"{tr.get('ms')}ms  {tr.get('text', '')}" if tr.get("text") else ""))
        tt = st.get("tts") or {}
        tm = M.get("tts") or {}
        N["tts"].set("active" if act("tts") else ("done" if rec("tts") else ""), tt.get("voice") or tm.get(tt.get("lang", "ja"), ""), "サーバー CPU",
                     "音声を合成中…" if act("tts") else (f"{tt.get('ms')}ms" if tt.get("ms") else ""))
        N["play"].set("active" if pl.get("state") == "active" else "", "StackChan(端末)", "スピーカー",
                      f"再生中 {pl.get('dur', '')}秒" if pl.get("state") == "active" else "待機")
        mood = "off" if not conn else "speak" if pl.get("state") == "active" else "think" if (act("translate") or act("stt") or act("tts")) \
            else "listen" if s.get("vad") else "idle"
        self.face.mood = mood
        self.sub.setText("// " + {"off": "LINK OFFLINE", "idle": "STANDBY", "listen": "LISTENING", "think": "PROCESSING", "speak": "SPEAKING"}[mood])
        lat = s.get("lat_med")
        self.summary.setText(f"翻訳モデル {tr.get('model') or s.get('model') or '-'}   /   最初の音まで 中央値 {lat if lat is not None else '-'}s   /   置き場所 {(s.get('mode') or '').upper()}")
        gpu = "s109" in (tr.get("where") or s.get("llm") or "")
        cands = "  ".join(
            f'<span style="color:{GR if (("s109" in c["label"]) == gpu) else "#eceff6"}">{"● " if (("s109" in c["label"]) == gpu) else ""}{c["short"]} · {place(c["label"])}</span>'
            for c in M.get("llm") or [])
        self.models.setText(
            f'<table cellspacing="6"><tr><td style="color:#9aa6bd">聞き取り</td><td>{sm.get("name", "-")} <span style="color:{where_color(sm.get("where", ""))}">● {place(sm.get("where", ""))}</span></td></tr>'
            f'<tr><td style="color:#9aa6bd">翻訳</td><td>{cands}</td></tr>'
            f'<tr><td style="color:#9aa6bd">声</td><td>日本語 {tm.get("ja", "-")} / 英語・中国語 {tm.get("en", "-")} / 韓国語 {tm.get("ko", "-")}</td></tr>'
            f'<tr><td style="color:#9aa6bd">顔検出</td><td>{M.get("face", "-")}</td></tr></table>')

        # 状態
        bt, dbg = s.get("batt"), s.get("dbg") or {}
        i = 0
        if bt:
            self.g_bat.set(float(bt.get("pct", 0)), f'{bt.get("pct")}%' + ("⚡" if bt.get("chg") else ""))
        if dbg.get("rssi") is not None:
            self.g_wifi.set(max(0.0, min(100.0, (dbg["rssi"] + 90) * 100 / 60)), f'{dbg["rssi"]}')
        up = s.get("uptime_s", 0)
        self.kv(self.tele, self.tele_w, "up", "UPTIME", f"{up // 3600}h {up % 3600 // 60}m", i); i += 1
        self.kv(self.tele, self.tele_w, "topic", "TOPIC", s.get("topic") or "—", i)
        y = s.get("sys") or {}
        if y.get("load"):
            cpus = y.get("cpus") or 1
            self.g_load.set(y["load"][0] * 100 / cpus, f'{y["load"][0]}')
            self.g_mem.set(float(y.get("mem_used_pct", 0)), f'{y.get("mem_used_pct")}%')
            self.kv(self.sysg, self.sys_w, "cpu", "CPU", f'{cpus} cores', 0)
        if y.get("temp_c") is not None:
            self.g_temp.set((y["temp_c"] - 30) * 100 / 70, f'{y["temp_c"]}℃')
        SN = {"sc-bridge": "ブリッジ", "sc-voicevox": "声", "sc-llm": "翻訳CPU", "sc-s109": "s109"}
        for k, v in (s.get("services") or {}).items():
            b = self.svc_b.get(k)
            if b is None:
                b = btn("", lambda k=k: self.restart(k))
                self.svc_b[k] = b
                self.svc_row.addWidget(b)
            b.setText(("● " if v == "active" else "○ ") + SN.get(k, k) + " ↻")
            b.setObjectName("" if v == "active" else "bad")
            b.style().unpolish(b)
            b.style().polish(b)
        ev = [f'{e["t"]}  {e["msg"]}' for e in s.get("events") or []]
        if [self.events.item(i).text() for i in range(self.events.count())] != ev:
            self.events.clear()
            self.events.addItems(ev or ["まだありません"])

        # 操作
        v = s.get("vol")
        if v is None and bt:
            v = bt.get("vol")
        if v is not None:
            self.vol.set_value(v)
        for m, b in self.eng_b.items():
            b.setChecked(m == s.get("mode"))
        pair = s.get("pair") or []
        for code, b in self.lang_b.items():
            b.setChecked(len(pair) > 1 and pair[1] == code)
        self.lat_lbl.setText(("最初の音まで " + " / ".join(str(x) for x in (s.get("lat") or [])) + " 秒") if lat is not None else "")
        self.spark.set(s.get("lat_all") or s.get("lat"))
        self.scope.level, self.scope.live = (s.get("mic") or {}).get("level", 0.0), bool(conn and (s.get("mic") or {}).get("live"))
        vs = s.get("voices") or []
        if vs and self.voice.count() != len(vs):
            self.voice.clear()
            for x in vs:
                self.voice.addItem(x["label"], x["id"])
        if vs and not self.voice.view().isVisible() and s.get("voice"):
            ix = self.voice.findData(int(s["voice"]))
            if ix >= 0 and ix != self.voice.currentIndex():
                self.voice.setCurrentIndex(ix)
        if s.get("speed") is not None:
            self.speed.set_value(s["speed"])
        if s.get("vad_th") is not None:
            self.vad.set_value(s["vad_th"])
        g = s.get("gate")
        if g:
            self.gate.set_value(g["mult"])
            snr = f'{20 * math.log10(g["speech"] / g["amb"]):.0f}' if g.get("amb") and g.get("speech") else "-"
            self.mic_lbl.setText(f'部屋の雑音 {g["amb"] * 100:.1f} / 声 {g["speech"] * 100:.1f} / 差 {snr}dB / ゲート {g["level"] * 100:.1f}'
                                 f'(捨てた {g["gated"]} 回) / 音割れ {g.get("clip", 0)}%')
        self.tg_track.setChecked(bool(s.get("track")))
        self.tg_listen.setChecked(bool(s.get("listen")))
        self.render_devices(s)
        self.render_discuss(s.get("discuss"))
        self.render_phrases()
        self.render_people(s.get("people"))
        self.render_turns()
        extra = f'   幻聴を除去 {s.get("halluc", 0)}'
        if s.get("lat_avg") is not None:
            extra += f'   最初の音まで 平均 {s["lat_avg"]}s 最速 {s["lat_best"]}s 最遅 {s["lat_worst"]}s'
        self.stats.setText(f'通訳回数 {s.get("turn_count", 0)}   表示中 {len(s.get("turns") or [])}{extra}')
        self.setWindowTitle(("● " if conn else "○ ") + "StackChan" + (" " + "↔".join(pair).upper() if conn and pair else ""))

    def render_turns(self, force=False):
        k = self.search.text().strip().lower()
        ts = [t for t in (self.cur.get("turns") or []) if not k or k in (t.get("orig", "") + " " + t.get("trans", "")).lower()]
        key = k + "|" + "|".join(t.get("ts", "") + t.get("orig", "") for t in ts)
        if key == self.turn_key and not force:
            return
        self.turn_key = key
        self.turns.clear()
        for t in ts:
            self.turns.addItem(f'{t.get("ts", "")[11:]}  {NAMES.get(t.get("from"), t.get("from"))} → {NAMES.get(t.get("to"), t.get("to"))} · {t.get("engine", "")}'
                               + (f' · {t["dev"]}' if t.get("dev") and len(self.cur.get("devices") or []) > 1 else "") + "\n"
                               + f'{t.get("orig", "")}\n→ {t.get("trans", "")}')
        if not ts:
            self.turns.addItem("該当なし")


def make_icon(path: str):
    pm = QPixmap(256, 256)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2.56, 2.56)
    p.setBrush(QColor("#171c28"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, 100, 100), 22, 22)
    p.setPen(QPen(QColor(CY), 3))
    p.drawLine(50, 8, 50, 18)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(MG))
    p.drawEllipse(QPointF(50, 7), 4, 4)
    p.setPen(QPen(QColor(CY), 3))
    p.setBrush(QColor("#232b3d"))
    p.drawRoundedRect(QRectF(14, 18, 72, 66), 20, 20)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(CY))
    for x in (28, 58):
        p.drawRoundedRect(QRectF(x, 38, 14, 20), 6, 6)
    p.drawRoundedRect(QRectF(38, 68, 24, 5), 2.5, 2.5)
    p.end()
    pm.save(path)


def main() -> int:
    shot = sys.argv[sys.argv.index("--offscreen-shot") + 1] if "--offscreen-shot" in sys.argv else ""
    if "--make-icon" in sys.argv:
        app = QApplication(sys.argv)
        make_icon(sys.argv[sys.argv.index("--make-icon") + 1])
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("StackChan")
    app.setDesktopFileName("stackchan")
    app.setStyleSheet(QSS)
    ip = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stackchan.png")
    if os.path.exists(ip):
        app.setWindowIcon(QIcon(ip))
    w = Win()
    if os.environ.get('SC_SIZE'):
        w.resize(*[int(x) for x in os.environ['SC_SIZE'].split('x')])
    w.show()
    if shot:                                               # 画面の確認用: 少し待って、各ページを画像にして終わる
        def grab():
            base = shot.rsplit(".", 1)[0]
            for i in range(w.tabs.count()):
                w.tabs.setCurrentIndex(i)
                QApplication.processEvents()
                w.grab().save(f"{base}_{i}.png")
                sa = w.tabs.widget(i)
                if isinstance(sa, QScrollArea):                # 下までスクロールした画像も撮る
                    sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
                    QApplication.processEvents()
                    w.grab().save(f"{base}_{i}b.png")
            app.quit()
        QTimer.singleShot(3500, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
