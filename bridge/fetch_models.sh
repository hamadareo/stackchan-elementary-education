#!/usr/bin/env bash
# 顔の識別モデル SFace(OpenCV 公式 zoo, 約37MB)を bridge/models に置く。無ければ取ってくる。
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)/models"
F="$D/face_recognition_sface_2021dec.onnx"
mkdir -p "$D"
[ -s "$F" ] || curl -fL --retry 3 -o "$F" https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
ls -l "$F"
