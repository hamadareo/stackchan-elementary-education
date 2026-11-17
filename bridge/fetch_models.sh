#!/usr/bin/env bash
# 顔の識別モデル SFace(OpenCV 公式 zoo, 約37MB)を bridge/models に置く。無ければ取ってくる。
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)/models"
F="$D/face_recognition_sface_2021dec.onnx"
mkdir -p "$D"
[ -s "$F" ] || curl -fL --retry 3 -o "$F" https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
ls -l "$F"

# 手の検出と関節の推定(じゃんけんをカメラで見分ける。OpenCV 公式 zoo の MediaPipe 由来・Apache-2.0、各 約4MB)。取れなくても声だけで遊べる
for M in palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx handpose_estimation_mediapipe/handpose_estimation_mediapipe_2023feb.onnx; do
  F="$D/$(basename "$M")"
  [ -s "$F" ] || curl -fL --retry 3 -o "$F" "https://github.com/opencv/opencv_zoo/raw/main/models/$M" || echo "(手のモデルを取得できませんでした: $M)"
done
ls -l "$D"
