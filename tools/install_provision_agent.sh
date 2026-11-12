#!/usr/bin/env bash
# Mac のログイン時に tools/provision.py --watch を自動で起動する(USB に StackChan を挿すだけで、書き込みから登録まで進む)。
#   bash tools/install_provision_agent.sh              登録して、すぐ起動
#   bash tools/install_provision_agent.sh --uninstall  解除
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.sokutsu.stackchan-provision"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "解除しました"
  exit 0
fi
PY="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$PY</string><string>-u</string><string>$ROOT/tools/provision.py</string><string>--watch</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>30</integer>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/local/bin:$HOME/.platformio/penv/bin</string></dict>
<key>StandardOutPath</key><string>$HOME/Library/Logs/stackchan-provision.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Logs/stackchan-provision.log</string>
</dict></plist>
PL
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "登録しました。ログ: ~/Library/Logs/stackchan-provision.log"
