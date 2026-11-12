#!/bin/bash
# s109 の GPU(聞き取り=9941 / 小さな翻訳モデル=9951)を、このサーバーの 12346 / 12345 へ転送し、落ちていれば s109 側を起こす。
# s109 に届かないときは、ブリッジが自動でこのサーバーの CPU に落ちる(auto)。
H=user@gpu-server
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new"
ensure() {
  $SSH $H "~/stackchan_llm/start.sh; curl -sf -m2 http://127.0.0.1:9941/health >/dev/null 2>&1 || (cd ~/tsuyaku_stt && setsid -f ~/ai_boss_local/stt-venv/bin/python s109_stt_server.py --port 9941 > stt.log 2>&1 < /dev/null)" </dev/null >/dev/null 2>&1
}
while true; do
  ensure
  $SSH -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      -L 127.0.0.1:12345:127.0.0.1:9951 -L 127.0.0.1:12346:127.0.0.1:9941 $H </dev/null &
  TP=$!
  while kill -0 $TP 2>/dev/null; do
    sleep 30
    curl -sf -m3 http://127.0.0.1:12345/v1/models >/dev/null 2>&1 && curl -sf -m3 http://127.0.0.1:12346/health >/dev/null 2>&1 || ensure
  done
  sleep 5
done
