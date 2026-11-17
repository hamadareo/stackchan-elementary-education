#!/usr/bin/env python3
"""ずんだもんのイラストから、StackChan の顔(firmware/src/zunda_art.h)を作る。

  python3 tools/make_zunda_art.py <イラスト.png> [--preview out.png]

- 口の部分を肌の色で消した絵(208x208 の RGB565)を C の配列として書き出す。口とまばたきは、機器の上で音声に合わせて描く。
- イラストの権利は元の作者にあります。公開・配布するときは、「東北ずん子・ずんだもんプロジェクト」の利用ガイドラインに従ってください
  (このリポジトリにイラスト本体(PNG)は含めない。変換後の配列だけを機器に入れる)。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

SIZE = 208                      # 機器に入れる絵の一辺
SRC = 512                       # 元の絵の一辺(想定)
S = SIZE / SRC
MOUTH_BOX = (186, 350, 262, 414)            # 元の絵での口の範囲(x0, y0, x1, y1): ここを肌の色で消す
EYES = [(153, 305), (290, 303)]             # 元の絵での目の中心(左, 右)
EYE_R = (34, 39)                            # 目をおおう楕円の半径(x, y)


def skin_color(im: Image.Image) -> tuple:
    """口のまわりの肌の色(輪の部分の中央値)。"""
    a = np.asarray(im)
    x0, y0, x1, y1 = MOUTH_BOX
    ring = np.concatenate([a[y0 - 6:y0 - 2, x0:x1].reshape(-1, 3), a[y0:y1, x0 - 6:x0 - 2].reshape(-1, 3), a[y0:y1, x1 + 2:x1 + 6].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(ring, axis=0))


def build(src: str) -> tuple:
    im = Image.open(src).convert("RGB")
    if im.size != (SRC, SRC):
        im = im.resize((SRC, SRC), Image.LANCZOS)
    skin = skin_color(im)
    d = ImageDraw.Draw(im)
    d.rectangle(MOUTH_BOX, fill=skin)                          # 口を消す
    bg = im.getpixel((4, 4))
    small = im.resize((SIZE, SIZE), Image.LANCZOS)
    return small, skin, bg, im


def rgb565(a: np.ndarray) -> np.ndarray:
    r, g, b = a[..., 0].astype(np.uint16), a[..., 1].astype(np.uint16), a[..., 2].astype(np.uint16)
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def header(small: Image.Image, skin: tuple, bg: tuple) -> str:
    px = rgb565(np.asarray(small)).flatten()
    rows = []
    for i in range(0, len(px), 16):
        rows.append("  " + ",".join(f"0x{v:04X}" for v in px[i:i + 16]) + ",")
    c = lambda t: f"{t[0]},{t[1]},{t[2]}"          # noqa: E731
    return (
        "// 自動生成: tools/make_zunda_art.py(ずんだもんのイラストから。口は消してあり、機器の上で音声に合わせて描く)\n"
        "// イラストの権利は元の作者にあります。公開・配布の前に「東北ずん子・ずんだもんプロジェクト」の利用ガイドラインを確認してください。\n"
        "#pragma once\n#include <stdint.h>\n"
        f"static const int ZART_SIZE = {SIZE};\n"
        f"static const uint8_t ZART_SKIN[3] = {{{c(skin)}}};\n"
        f"static const uint8_t ZART_BG[3] = {{{c(bg)}}};\n"
        f"static const int ZART_MOUTH_CX = {round(((MOUTH_BOX[0] + MOUTH_BOX[2]) / 2 - 4) * S)};\n"
        f"static const int ZART_MOUTH_TOP = {round(359 * S)};\n"
        f"static const int ZART_MOUTH_W = {round(55 * S)};\n"
        f"static const int ZART_MOUTH_H = {round(48 * S)};\n"
        f"static const int ZART_EYE_X[2] = {{{round(EYES[0][0] * S)}, {round(EYES[1][0] * S)}}};\n"
        f"static const int ZART_EYE_Y[2] = {{{round(EYES[0][1] * S)}, {round(EYES[1][1] * S)}}};\n"
        f"static const int ZART_EYE_RX = {round(EYE_R[0] * S)};\nstatic const int ZART_EYE_RY = {round(EYE_R[1] * S)};\n"
        "static const uint16_t ZART_PX[ZART_SIZE * ZART_SIZE] = {\n" + "\n".join(rows) + "\n};\n")


def preview(small: Image.Image, skin: tuple, path: str) -> None:
    """機器の上で描くのと同じ形(口の開き具合 0 / 0.5 / 1、まばたき、わらった目)を並べた確認用の画像。"""
    frames = []
    for state in ("closed", "half", "open", "blink", "happy"):
        f = small.copy()
        d = ImageDraw.Draw(f)
        ol, red = (70, 45, 50), (209, 126, 127)
        cx, top = round(((MOUTH_BOX[0] + MOUTH_BOX[2]) / 2 - 4) * S), round(359 * S)
        r = {"closed": 0.0, "half": 0.5, "open": 1.0, "blink": 0.5, "happy": 1.0}[state]
        if r < 0.1:
            d.arc((cx - 7, top + 1, cx + 7, top + 13), 20, 160, fill=ol, width=2)
        else:
            w, h = round(16 + 8 * r), round(7 + 14 * r)
            d.rectangle((cx - w // 2, top, cx + w // 2, top + h // 2), fill=ol)
            d.ellipse((cx - w // 2, top, cx + w // 2, top + h), fill=ol)
            d.rectangle((cx - w // 2 + 2, top + 2, cx + w // 2 - 2, top + h // 2), fill=red)
            d.ellipse((cx - w // 2 + 2, top + 2, cx + w // 2 - 2, top + h - 2), fill=red)
        if state in ("blink", "happy"):
            for ex, ey in zip([round(e[0] * S) for e in EYES], [round(e[1] * S) for e in EYES]):
                d.ellipse((ex - round(EYE_R[0] * S), ey - round(EYE_R[1] * S), ex + round(EYE_R[0] * S), ey + round(EYE_R[1] * S)), fill=skin)
                if state == "blink":
                    d.arc((ex - 12, ey - 14, ex + 12, ey + 10), 20, 160, fill=ol, width=3)
                else:
                    d.arc((ex - 12, ey - 2, ex + 12, ey + 22), 200, 340, fill=ol, width=3)
        frames.append(f)
    sheet = Image.new("RGB", (SIZE * len(frames), SIZE))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * SIZE, 0))
    sheet.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--preview", default="")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware", "src", "zunda_art.h"))
    a = ap.parse_args()
    small, skin, bg, _full = build(a.src)
    open(a.out, "w").write(header(small, skin, bg))
    print(f"書き出し: {a.out}  肌 {skin}  背景 {bg}")
    if a.preview:
        preview(small, skin, a.preview)
        print("確認用:", a.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
