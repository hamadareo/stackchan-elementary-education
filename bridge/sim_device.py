#!/usr/bin/env python3
"""擬似 StackChan(機器 ID つき)。複数台・自動セットアップの確認用。受け取った指示を表示し、しばらくつなぎ続ける。

  bridge/.venv/bin/python bridge/sim_device.py --id aabbccddeeff [--url ws://サーバー:8765] [--secs 40]
"""
import argparse
import asyncio
import json

import websockets


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--secs", type=float, default=40.0)
    a = ap.parse_args()
    async with websockets.connect(a.url, max_size=2 ** 22) as ws:
        await ws.send(json.dumps({"t": "hello", "id": a.id, "fw": "sim", "pair": ["ja", "en"]}))
        await ws.send(json.dumps({"t": "batt", "pct": 77, "v": 3.9, "chg": False, "vol": 120}))

        async def rx():
            async for m in ws:
                if isinstance(m, str):
                    j = json.loads(m)
                    if j.get("t") not in ("status",):
                        print("<-", json.dumps(j, ensure_ascii=False)[:140], flush=True)
        try:
            await asyncio.wait_for(rx(), a.secs)
        except asyncio.TimeoutError:
            pass


asyncio.run(main())
