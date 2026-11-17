"""カメラ画像から、じゃんけんの手(ぐー・ちょき・ぱー)を見分ける。

手の検出と 21 点の関節の推定には、OpenCV 公式 zoo の MediaPipe 由来のモデルを使う(cv2.dnn で動く。追加の pip は不要)。
  - palm_detection_mediapipe_2023feb.onnx / handpose_estimation_mediapipe_2023feb.onnx(Apache-2.0)
  - 前処理・後処理は opencv_zoo の mp_palmdet.py / mp_handpose.py(Apache-2.0, Copyright OpenCV contributors)を、
    中身を読んで確認したうえで取り込んだもの。アンカー(2016 個)は、元のファイルの長い配列の代わりに規則から作る。
モデルは bridge/fetch_models.sh が bridge/models に置く。無ければ(または cv2 が無ければ)、カメラ認識は使えない(声だけで遊べる)。

指が立っているかは、手首からの距離(指先 > 第二関節)で見る。回転しても、手の向きが違っても同じ判定になる。
"""
from __future__ import annotations

import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PALM_MODEL = os.path.join(HERE, "models", "palm_detection_mediapipe_2023feb.onnx")
POSE_MODEL = os.path.join(HERE, "models", "handpose_estimation_mediapipe_2023feb.onnx")

# 指ごとの (付け根, 第二関節, 指先) の関節番号。0 は手首。親指は 1-4。
FINGERS = {"index": (5, 6, 8), "middle": (9, 10, 12), "ring": (13, 14, 16), "pinky": (17, 18, 20)}


# ---------------------------------------------------------------- 指の数 → 手
def finger_states(lm: np.ndarray) -> dict:
    """21 点(x, y[, z])から、各指が立っているか。手首からの距離で比べる(手の向きに依存しない)。"""
    p = np.asarray(lm, dtype=np.float64)[:, :2]
    w = p[0]
    d = lambda i: float(np.linalg.norm(p[i] - w))      # noqa: E731
    out = {}
    for name, (_mcp, pip, tip) in FINGERS.items():
        out[name] = d(tip) > d(pip) * 1.12              # 指先が第二関節より、はっきり遠い
    # 親指: 指先が、人差し指の付け根より手首から遠く、かつ小指の付け根から離れている(たたんだ親指は手のひら側に寄る)
    palm = float(np.linalg.norm(p[5] - p[17])) or 1.0
    out["thumb"] = d(4) > d(3) * 1.1 and float(np.linalg.norm(p[4] - p[17])) > palm * 0.95
    return out


def classify(states: dict) -> "str | None":
    """指の状態 → gu / choki / pa。決められない形(指1本だけ立てる等)は None。"""
    four = [states[k] for k in ("index", "middle", "ring", "pinky")]
    n = sum(four)
    if n == 4 or (n == 3 and states.get("thumb")):
        return "pa"
    if states["index"] and states["middle"] and not states["ring"] and not states["pinky"]:
        return "choki"
    if n == 0 or (n == 1 and not states["index"] and not states["middle"]):      # 小指・薬指だけ少し浮いた握りこぶしも、ぐー
        return "gu"
    return None


def classify_landmarks(lm) -> "str | None":
    return classify(finger_states(lm))


def vote(hands: list, need: int = 2) -> "str | None":
    """連続フレームの多数決。need 票以上で、いちばん多い手(同数なら決めない)。"""
    hs = [h for h in hands if h]
    if not hs:
        return None
    counts = {h: hs.count(h) for h in set(hs)}
    best = max(counts.values())
    top = [h for h, c in counts.items() if c == best]
    if len(top) != 1:
        return None
    if best >= need:
        return top[0]
    return top[0] if len(counts) == 1 else None          # 検出が少なくても、見えた手が食い違わなければ採用する(画像が少ない・手が小さい対策)


_REC = None
_REC_LOCK = __import__("threading").Lock()          # cv2.dnn のネットは同時に使えないので、複数の機器で順番に使う


def shared() -> "HandRecognizer | None":
    """共有の認識器(初回に読み込む)。モデルや cv2 が無ければ None。"""
    global _REC
    with _REC_LOCK:
        if _REC is None and HandRecognizer.available():
            try:
                _REC = HandRecognizer()
            except Exception as e:  # noqa: BLE001
                print(f"[bridge] 手のモデルを読めません: {type(e).__name__}: {e}", flush=True)
                _REC = False
        return _REC or None


def recognize_jpegs(jpegs: list) -> "tuple[str | None, list]":
    rec = shared()
    if rec is None:
        return None, []
    with _REC_LOCK:
        return rec.recognize_jpegs(jpegs)


# ---------------------------------------------------------------- 検出(opencv_zoo 由来)
def make_anchors() -> np.ndarray:
    """MediaPipe 手のひら検出のアンカー: 192x192 の入力に対し、stride 8 の 24x24 格子×2 と stride 16 の 12x12 格子×6。"""
    out = []
    for grid, per in ((24, 2), (12, 6)):
        for y in range(grid):
            for x in range(grid):
                out.extend([[(x + 0.5) / grid, (y + 0.5) / grid]] * per)
    return np.array(out, dtype=np.float32)


class HandRecognizer:
    """画像(BGR)から、いちばん確かな手を 1 つ見つけ、ぐー/ちょき/ぱー を返す。"""

    def __init__(self, palm_path: str = PALM_MODEL, pose_path: str = POSE_MODEL):
        import cv2
        self.cv = cv2
        self.palm = cv2.dnn.readNet(palm_path)
        self.pose = cv2.dnn.readNet(pose_path)
        self.anchors = make_anchors()
        self.palm_size = np.array([192, 192])
        self.pose_size = np.array([224, 224])
        self.palm_score, self.palm_nms, self.pose_conf = 0.4, 0.3, 0.6

    @staticmethod
    def available() -> bool:
        try:
            import cv2  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return os.path.exists(PALM_MODEL) and os.path.exists(POSE_MODEL)

    # ---- 手のひらの検出
    def detect_palms(self, img: np.ndarray) -> np.ndarray:
        cv = self.cv
        h, w = img.shape[:2]
        ratio = min(self.palm_size / np.array([h, w]))
        pad = np.array([0.0, 0.0])
        x = img
        if (h, w) != tuple(self.palm_size):
            rs = (np.array([h, w]) * ratio).astype(np.int32)
            x = cv.resize(img, (rs[1], rs[0]))
            ph, pw = self.palm_size[0] - rs[0], self.palm_size[1] - rs[1]
            pad = np.array([pw // 2, ph // 2], dtype=np.float64)
            x = cv.copyMakeBorder(x, ph // 2, ph - ph // 2, pw // 2, pw - pw // 2, cv.BORDER_CONSTANT, None, (0, 0, 0))
        blob = (cv.cvtColor(x, cv.COLOR_BGR2RGB).astype(np.float32) / 255.0)[np.newaxis]
        pad_bias = (pad / ratio).astype(np.int32)
        self.palm.setInput(blob)
        out = self.palm.forward(self.palm.getUnconnectedOutLayersNames())
        score = 1 / (1 + np.exp(-out[1][0, :, 0].astype(np.float64)))
        delta = out[0][0, :, 0:4]
        lmd = out[0][0, :, 4:]
        scale = max(w, h)
        cxy, wh = delta[:, :2] / self.palm_size, delta[:, 2:] / self.palm_size
        boxes = np.concatenate([(cxy - wh / 2 + self.anchors) * scale, (cxy + wh / 2 + self.anchors) * scale], axis=1)
        boxes -= [pad_bias[0], pad_bias[1], pad_bias[0], pad_bias[1]]
        keep = cv.dnn.NMSBoxes(boxes, score, self.palm_score, self.palm_nms, top_k=5000)
        if len(keep) == 0:
            return np.empty((0, 19))
        keep = np.array(keep).reshape(-1)
        lms = lmd[keep].reshape(-1, 7, 2) / self.palm_size
        lms = (lms + self.anchors[keep][:, None, :]) * scale - pad_bias
        return np.c_[boxes[keep].reshape(-1, 4), lms.reshape(-1, 14), score[keep].reshape(-1, 1)]

    # ---- 21 点の推定
    def _crop_pad(self, image, bbox, for_rotation: bool):
        cv = self.cv
        wh = bbox[1] - bbox[0]
        bbox = bbox + (np.array([0, 0]) if for_rotation else np.array([0, -0.4])) * wh
        c = np.sum(bbox, axis=0) / 2
        half = (bbox[1] - bbox[0]) * (4 if for_rotation else 3) / 2
        bbox = np.array([c - half, c + half]).astype(np.int32)
        bbox[:, 0] = np.clip(bbox[:, 0], 0, image.shape[1])
        bbox[:, 1] = np.clip(bbox[:, 1], 0, image.shape[0])
        image = image[bbox[0][1]:bbox[1][1], bbox[0][0]:bbox[1][0], :]
        side = int(np.linalg.norm(image.shape[:2])) if for_rotation else int(max(image.shape[:2]))
        ph, pw = side - image.shape[0], side - image.shape[1]
        left, top = pw // 2, ph // 2
        image = cv.copyMakeBorder(image, top, ph - top, left, pw - left, cv.BORDER_CONSTANT, None, (0, 0, 0))
        return image, bbox, bbox[0] - [left, top]

    def landmarks(self, img: np.ndarray, palm: np.ndarray) -> "np.ndarray | None":
        """手のひら 1 つ(detect_palms の 1 行)から、画像上の 21 点(x, y, z)。自信が低ければ None。"""
        cv = self.cv
        bbox = palm[0:4].reshape(2, 2).copy()
        crop, bbox, bias = self._crop_pad(img, bbox, True)
        if crop.size == 0:
            return None
        crop = cv.cvtColor(crop, cv.COLOR_BGR2RGB)
        pad_bias = np.array(bias, dtype=np.int32)
        bbox = bbox - pad_bias
        pl = palm[4:18].reshape(7, 2) - pad_bias
        p1, p2 = pl[0], pl[2]
        rad = np.pi / 2 - np.arctan2(-(p2[1] - p1[1]), p2[0] - p1[0])
        rad = rad - 2 * np.pi * np.floor((rad + np.pi) / (2 * np.pi))
        angle = np.rad2deg(rad)
        center = np.sum(bbox, axis=0) / 2
        rm = cv.getRotationMatrix2D(tuple(float(v) for v in center), angle, 1.0)
        rot = cv.warpAffine(crop, rm, (crop.shape[1], crop.shape[0]))
        hom = np.c_[pl, np.ones(pl.shape[0])]
        rl = np.array([np.dot(hom, rm[0]), np.dot(hom, rm[1])])
        rbox = np.array([np.amin(rl, axis=1), np.amax(rl, axis=1)])
        crop2, rbox, _ = self._crop_pad(rot, rbox, False)
        if crop2.size == 0:
            return None
        blob = (cv.resize(crop2, tuple(int(v) for v in self.pose_size), interpolation=cv.INTER_AREA).astype(np.float32) / 255.0)[np.newaxis]
        self.pose.setInput(blob)
        lm, conf, _hd, _lw = self.pose.forward(self.pose.getUnconnectedOutLayersNames())
        if float(conf[0][0]) < self.pose_conf:
            return None
        lm = lm[0].reshape(-1, 3).astype(np.float64)
        sf = max((rbox[1] - rbox[0]) / self.pose_size)
        lm[:, :2] = (lm[:, :2] - self.pose_size / 2) * sf
        cm = cv.getRotationMatrix2D((0, 0), angle, 1.0)
        rotated = np.dot(lm[:, :2], cm[:, :2])
        rc = np.array([[rm[0][0], rm[1][0]], [rm[0][1], rm[1][1]]])
        tr = np.array([rm[0][2], rm[1][2]])
        inv = np.c_[rc, np.array([-np.dot(rc[0], tr), -np.dot(rc[1], tr)])]
        cen = np.append(np.sum(rbox, axis=0) / 2, 1)
        orig = np.array([np.dot(cen, inv[0]), np.dot(cen, inv[1])])
        lm[:, :2] = rotated + orig + pad_bias
        return lm

    def recognize(self, img: np.ndarray) -> "tuple[str | None, float]":
        """画像 → (手 gu/choki/pa/None, 手のひらの確からしさ)。手が見つからなければ (None, 0)。"""
        if img is None or img.size == 0:
            return None, 0.0
        palms = self.detect_palms(img)
        if len(palms) == 0:
            return None, 0.0
        palm = max(palms, key=lambda r: r[18])
        lm = self.landmarks(img, palm)
        if lm is None:
            return None, float(palm[18])
        return classify_landmarks(lm), float(palm[18])

    def decode(self, jpeg: bytes):
        return self.cv.imdecode(np.frombuffer(jpeg, np.uint8), self.cv.IMREAD_COLOR)

    def recognize_jpegs(self, jpegs: list) -> "tuple[str | None, list]":
        """JPEG のフレーム列 → 多数決の手。"""
        return self.recognize_frames([self.decode(j) for j in jpegs])

    def recognize_frames(self, imgs: list) -> "tuple[str | None, list]":
        """連続フレームの多数決。(手, 各フレームの結果)。"""
        per = []
        for im in imgs:
            try:
                per.append(self.recognize(im)[0])
            except Exception:  # noqa: BLE001 - 1 枚の失敗で止めない
                per.append(None)
        return vote(per), per


# ---------------------------------------------------------------- 管理画面の「手の確認」(目で見て確かめる)
LABEL = {"gu": "ぐー", "choki": "ちょき", "pa": "ぱー"}
# 関節の線(手首 0 → 各指)。MediaPipe の 21 点
_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
          (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


def status_text(has_frame: bool, n_palm: int, hand: "str | None") -> str:
    """確認エリアの大きな文字。画像なし / 手なし / 手は見えるが決められない / ぐー・ちょき・ぱー。"""
    if not has_frame:
        return "画像が届いていません"
    if n_palm <= 0:
        return "手なし"
    return LABEL.get(hand or "", "決められない")


def draw_overlay(img: np.ndarray, palm, lm, text: str) -> np.ndarray:
    """検出した手のひらの枠と、21 点の関節・指の線、判定の文字を画像に重ねる(元の画像は変えない)。"""
    import cv2 as cv
    out = img.copy()
    if palm is not None:
        x1, y1, x2, y2 = [int(v) for v in palm[:4]]
        cv.rectangle(out, (x1, y1), (x2, y2), (255, 200, 0), 1)
    if lm is not None:
        pts = [(int(p[0]), int(p[1])) for p in lm]
        for a, b in _BONES:
            cv.line(out, pts[a], pts[b], (0, 255, 120), 1, cv.LINE_AA)
        for i, p in enumerate(pts):
            cv.circle(out, p, 2, (0, 120, 255) if i in (4, 8, 12, 16, 20) else (255, 255, 255), -1)
    return out


def analyze_jpeg(jpeg: bytes) -> "tuple[bytes | None, dict]":
    """カメラの JPEG → (重ね描き済み JPEG, {"hand","n_palm","text"})。モデルが無ければ元の画像のまま hand=None。"""
    import cv2 as cv
    info = {"hand": None, "n_palm": 0, "text": status_text(bool(jpeg), 0, None)}
    if not jpeg:
        return None, info
    img = cv.imdecode(np.frombuffer(jpeg, np.uint8), cv.IMREAD_COLOR)
    if img is None:
        return None, {"hand": None, "n_palm": 0, "text": "画像が届いていません"}
    rec = shared()
    palm = lm = None
    if rec is not None:
        with _REC_LOCK:
            try:
                palms = rec.detect_palms(img)
                info["n_palm"] = int(len(palms))
                if len(palms):
                    palm = max(palms, key=lambda r: r[18])
                    lm = rec.landmarks(img, palm)
                    if lm is not None:
                        info["hand"] = classify_landmarks(lm)
            except Exception:  # noqa: BLE001
                pass
    info["text"] = status_text(True, info["n_palm"], info["hand"])
    ok, buf = cv.imencode(".jpg", draw_overlay(img, palm, lm, info["text"]), [cv.IMWRITE_JPEG_QUALITY, 80])
    return (bytes(buf) if ok else None), info
