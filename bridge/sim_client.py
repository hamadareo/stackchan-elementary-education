#!/usr/bin/env python3
"""実機なしで動作確認する疑似 StackChan。WAV(16kHz mono)をマイクの代わりに送り、返ってきた
字幕・表情・読み上げ音声を表示/保存する。

  bridge/.venv/bin/python bridge/sim_client.py input.wav [--url ws://127.0.0.1:8765] [--pair ja-en] [--out reply.wav]
"""
import argparse
import asyncio
import json
import time
import wave

import numpy as np
import websockets


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--pair", default="ja-en")
    ap.add_argument("--out", default="reply.wav")
    ap.add_argument("--tail", type=float, default=12.0, help="送り終えてから返事を待つ秒数")
    a = ap.parse_args()

    with wave.open(a.wav, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "16kHz mono が必要"
        pcm = w.readframes(w.getnframes())
    reply = bytearray()
    t0 = time.time()

    async with websockets.connect(a.url, max_size=2 ** 22) as ws:
        await ws.send(json.dumps({"t": "hello", "pair": a.pair.split("-")}))

        async def rx():
            async for m in ws:
                if isinstance(m, bytes):
                    if m[0] == 0x03:
                        reply.extend(m[1:])
                    continue
                j = json.loads(m)
                if j["t"] == "face":
                    print(f"[{time.time()-t0:5.1f}s] 顔: {j['state']}")
                elif j["t"] == "caption" and j.get("final"):
                    print(f"[{time.time()-t0:5.1f}s] 字幕 {j['lang']}→{j.get('to','')}: {j['orig']} ⇝ {j['trans']}")
                elif j["t"] in ("speech", "speech_end", "hello_ack", "look"):
                    print(f"[{time.time()-t0:5.1f}s] {j}")

        task = asyncio.create_task(rx())
        step = 3200  # 0.1秒 = 実時間でマイクの代わりに送る
        for i in range(0, len(pcm), step * 2):
            await ws.send(b"\x01" + pcm[i:i + step * 2])
            await asyncio.sleep(0.1)
        for _ in range(20):                      # 末尾に2秒の無音(実時間で。端末は常にマイクを送り続ける)
            await ws.send(b"\x01" + bytes(3200))
            await asyncio.sleep(0.1)
        await asyncio.sleep(a.tail)
        task.cancel()

    if reply:
        with wave.open(a.out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(bytes(reply))
        print(f"返事の音声 {len(reply)/32000:.1f}秒 → {a.out}")
    else:
        print("返事の音声なし")


asyncio.run(main())
