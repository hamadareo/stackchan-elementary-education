#!/usr/bin/env python3
"""新しい StackChan を自動セットアップする(Mac で実行)。何もしていない新品でも、USB に挿すだけ。

  python3 tools/provision.py --watch            常に待ち受け、USB に機器を挿すたびに自動で処理する(Ctrl+C で終了)
  python3 tools/provision.py                     いま USB につながっている機器 1 台を処理する
  python3 tools/provision.py --port /dev/cu.usbmodem101 [--force]
  tools/install_provision_agent.sh               Mac のログイン時に --watch を自動で起動する(挿すだけで動くようにする)

機器を挿すと:
  ① 機器の MAC アドレスを読み、すでに登録済み(Ubuntu の台帳にある)ならそのままにする
  ② 新しい機器なら Sokutsu のファームウェアを書き込む(Wi-Fi 設定は firmware/src/config.h)
     書き込みモードに入れないときは、RST の長押しをお願いして、自動でやり直す
  ③ 再起動した機器が Wi-Fi につながり、ブリッジへ自動登録されるのを待つ(名前・音量などは自動設定)
経過と結果は macOS の通知で知らせる。書き込んだあとは USB を抜いて構いません。

環境変数: PIO(pio のパス) / SC_ADMIN_URL(既定 http://localhost:8080)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
ADMIN = os.environ.get("SC_ADMIN_URL", "http://localhost:8080").rstrip("/")
PORT_GLOBS = os.environ.get("SC_PORT_GLOBS", "/dev/cu.usbmodem*:/dev/ttyACM*").split(":")     # ESP32-S3 の USB(CoreS3)
PENV = os.path.expanduser("~/.platformio/penv/bin")
ESPTOOL = os.path.expanduser("~/.platformio/packages/tool-esptoolpy/esptool.py")


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def notify(title: str, msg: str) -> None:
    """macOS の通知(Mac 以外では何もしない)。"""
    if sys.platform != "darwin":
        return
    msg = msg.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}"'], capture_output=True)


def ports() -> set:
    out = set()
    for g in PORT_GLOBS:
        out.update(glob.glob(g))
    return out


def find_pio() -> str:
    for c in (os.environ.get("PIO"), shutil.which("pio"), shutil.which("platformio"), os.path.join(PENV, "pio")):
        if c and os.path.exists(c):
            return c
    sys.exit("pio(PlatformIO)が見つかりません。環境変数 PIO にパスを指定してください")


def parse_mac(text: str) -> str:
    """esptool の出力から MAC を取り出し、台帳の ID と同じ形(小文字・コロンなし)にする。"""
    m = re.search(r"MAC:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", text or "")
    return m.group(1).replace(":", "").lower() if m else ""


def read_mac(port: str) -> str:
    """USB 越しに機器の MAC を読む(読めなければ空)。読んだあと機器は再起動する。"""
    py = os.path.join(PENV, "python")
    if not (os.path.exists(py) and os.path.exists(ESPTOOL)):
        return ""
    try:
        r = subprocess.run([py, ESPTOOL, "--chip", "esp32s3", "--port", port, "read_mac"], capture_output=True, text=True, timeout=40)
        return parse_mac(r.stdout + r.stderr)
    except Exception:  # noqa: BLE001
        return ""


def devices() -> list:
    try:
        return json.load(urllib.request.urlopen(ADMIN + "/api/devices", timeout=4))
    except Exception:  # noqa: BLE001
        return []


def is_registered(mac: str, devs: list) -> bool:
    return bool(mac) and any(d.get("id") == mac for d in devs)


def flash(port: str, dry: bool = False) -> bool:
    if not os.path.exists(os.path.join(FW, "src", "config.h")):
        sys.exit("firmware/src/config.h がありません(Wi-Fi の設定。config.h.example を写して編集してください)")
    cmd = [find_pio(), "run", "-e", "m5stack-cores3", "-t", "upload", "--upload-port", port]
    log(f"書き込み: {port}")
    if dry:
        log("  (予行)", " ".join(cmd))
        return True
    return subprocess.run(cmd, cwd=FW).returncode == 0


def flash_with_retry(port: str, dry: bool, tries: int = 3) -> bool:
    """書き込みモードに入れない新品がある。失敗したら RST 長押しをお願いして、ポートが現れ直すのを待ってやり直す。"""
    for i in range(tries):
        if flash(port, dry):
            return True
        if i == tries - 1:
            break
        notify("StackChan", "書き込みモードに入れません。RST ボタンを 2 秒長押ししてください(緑のランプが点きます)")
        log("書き込めませんでした。RST ボタンを 2 秒長押ししてください(自動でやり直します)")
        t0 = time.time()
        gone = False
        while time.time() - t0 < 45:                         # 機器が再接続され、ポートが現れ直すのを待つ
            time.sleep(1.0)
            ps = sorted(ports())
            if not ps:
                gone = True
            elif gone or time.time() - t0 > 8:
                port = ps[0]
                break
    return False


def wait_registered(before: set, timeout: float = 120.0) -> "dict | None":
    """書き込み後、台帳に新しい機器(または再接続した機器)が現れるのを待つ。"""
    t0 = time.time()
    log("機器が Wi-Fi につながり、自動登録されるのを待っています…")
    while time.time() - t0 < timeout:
        for d in devices():
            if d["id"] not in before and d.get("online"):
                return d
        time.sleep(3)
    return None


def provision(port: str, dry: bool = False, force: bool = False) -> str:
    """1 台を処理する。戻り値: "done" / "skipped"(登録済み) / "failed"。"""
    devs = devices()
    mac = read_mac(port)
    if mac:
        log(f"機器を見つけました: MAC {mac}")
    if mac and is_registered(mac, devs) and not force:
        log("すでに登録されている機器です。そのままにします")
        return "skipped"
    notify("StackChan", "新しい機器を見つけました。ファームウェアを書き込みます(約 1 分)")
    before = {d["id"] for d in devs}
    if not flash_with_retry(port, dry):
        notify("StackChan", "書き込みに失敗しました。USB を挿し直して、もう一度試してください")
        log("✗ 書き込みに失敗しました")
        return "failed"
    if dry:
        return "done"
    d = wait_registered(before)
    if d:
        notify("StackChan", f"{d['name']} を登録しました。USB を抜いて構いません")
        log(f"✓ 登録されました: {d['name']}  ({d['ip']})  — 管理画面の「機器」に出ています")
    else:
        notify("StackChan", "書き込みは終わりました。管理画面の「機器」を確認してください")
        log("△ 書き込みは終わりましたが、登録を確認できませんでした(Wi-Fi の設定を確認してください)")
    return "done"


def warm_up() -> None:
    """挿した直後に早く書き込めるよう、先にビルドしておく(初回だけ時間がかかる)。"""
    try:
        subprocess.run([find_pio(), "run", "-e", "m5stack-cores3"], cwd=FW, capture_output=True, timeout=900)
        log("ファームウェアの準備ができました")
    except Exception as e:  # noqa: BLE001
        log(f"(事前ビルドを省略: {e})")


def watch(dry: bool = False) -> None:
    log("待ち受け中: 新しい StackChan を USB につなぐと、自動でセットアップします(Ctrl+C で終了)")
    if not dry:
        warm_up()
    handled: dict = {}                                    # ポート → 無視する時刻(処理した直後は、再接続で現れ直しても触らない)
    seen: set = set()
    while True:
        now = ports()
        for p in sorted(now - seen):
            if handled.get(p, 0) > time.time():
                continue
            time.sleep(2.0)                               # ポートが安定するのを待つ
            provision(p, dry)
            handled[p] = time.time() + 120.0
        seen = ports()
        time.sleep(1.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="常に待ち受け、挿すたびに自動で処理する")
    ap.add_argument("--port")
    ap.add_argument("--force", action="store_true", help="登録済みの機器でも書き込む")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず、手順だけ表示する")
    a = ap.parse_args()
    if a.watch:
        watch(a.dry_run)
        return 0
    if a.port:
        return 0 if provision(a.port, a.dry_run, a.force) != "failed" else 1
    ps = sorted(ports())
    if not ps:
        print("USB につながった機器が見つかりません。ケーブルを確認するか、--watch で待ち受けてください")
        return 1
    if len(ps) > 1:
        print("複数の USB 機器があります。--port で指定してください:", *ps)
        return 1
    return 0 if provision(ps[0], a.dry_run, a.force) != "failed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
