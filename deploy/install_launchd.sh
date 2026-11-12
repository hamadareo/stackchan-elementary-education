#!/usr/bin/env bash
# Mac のログイン時にブリッジを自動起動する(launchd)。USB でつながっていれば USB、無ければ Wi-Fi だけで待つ。
# 使い方: bash deploy/install_launchd.sh install | uninstall | status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="local.sokutsu-stackchan"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/sokutsu-stackchan.log"
case "${1:-status}" in
  install)
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    SER=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)
    ARGS="<string>$ROOT/bridge/run.sh</string>"
    [[ -n "$SER" ]] && ARGS="$ARGS<string>--serial</string><string>$SER</string>"
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string>$ARGS</array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "登録しました: $PLIST (ログ: $LOG)";;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"; echo "解除しました";;
  *) launchctl list | grep "$LABEL" || echo "未登録";;
esac
