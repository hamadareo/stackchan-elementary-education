#!/usr/bin/env python3
"""登録済みの全機器を、無線(OTA)でまとめて更新する(Mac で実行)。

  SC_OTA_PASS=<config.h の OTA_PASS> python3 tools/ota_all.py [--only 名前] [--dry-run]

機器の一覧は Ubuntu の管理画面(/api/devices)から取る。接続中で IP が分かる機器だけを、1 台ずつ書き込む。
SC_OTA_PASS が未設定なら firmware/src/config.h から読む。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
ADMIN = os.environ.get("SC_ADMIN_URL", "http://localhost:8080").rstrip("/")


def ota_pass() -> str:
    p = os.environ.get("SC_OTA_PASS", "")
    if p:
        return p
    try:
        m = re.search(r'#define\s+OTA_PASS\s+"([^"]*)"', open(os.path.join(FW, "src", "config.h"), encoding="utf-8").read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def targets(devs: list, only: str = "") -> list:
    return [d for d in devs if d.get("online") and d.get("ip") and d["ip"] != "usb" and not d["ip"].startswith("ip-")
            and (not only or only in (d["name"], d["id"]))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="この名前(または ID)の機器だけ")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pio = os.environ.get("PIO") or shutil.which("pio") or shutil.which("platformio")
    if not pio:
        sys.exit("pio が見つかりません。環境変数 PIO にパスを指定してください")
    devs = json.load(urllib.request.urlopen(ADMIN + "/api/devices", timeout=5))
    ts = targets(devs, a.only)
    if not ts:
        print("更新できる機器がありません(接続中の機器だけが対象です)")
        return 1
    pw = ota_pass()
    if not pw and not a.dry_run:
        sys.exit("OTA のパスワードがありません(SC_OTA_PASS か firmware/src/config.h)")
    ok = 0
    for d in ts:
        print(f"→ {d['name']} ({d['ip']})", flush=True)
        if a.dry_run:
            continue
        r = subprocess.run([pio, "run", "-e", "ota", "-t", "upload", "--upload-port", d["ip"]], cwd=FW, env={**os.environ, "SC_OTA_PASS": pw})
        print("  ✓ 完了" if r.returncode == 0 else "  ✗ 失敗", flush=True)
        ok += r.returncode == 0
    if not a.dry_run:
        print(f"{ok}/{len(ts)} 台を更新しました")
    return 0 if a.dry_run or ok == len(ts) else 1


if __name__ == "__main__":
    sys.exit(main())
