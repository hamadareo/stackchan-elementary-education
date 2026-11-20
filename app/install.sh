#!/usr/bin/env bash
# Ubuntu 上で実行: 管理アプリのアイコンとランチャー(アプリ一覧・ドック)を登録する。
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
PY="$D/../.venv-gui/bin/python"
QT_QPA_PLATFORM=offscreen "$PY" "$D/stackchan_admin.py" --make-icon "$D/stackchan.png" 2>/dev/null
cat > "$D/run.sh" <<RUN
#!/usr/bin/env bash
export QT_QPA_PLATFORM="\${QT_QPA_PLATFORM:-wayland;xcb}"
[ -d "$D/../xcblib" ] && export LD_LIBRARY_PATH="$D/../xcblib/usr/lib/x86_64-linux-gnu:\${LD_LIBRARY_PATH:-}"
exec "$PY" "$D/stackchan_admin.py" "\$@"
RUN
chmod +x "$D/run.sh"
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/stackchan.desktop <<DESK
[Desktop Entry]
Type=Application
Name=StackChan
Comment=StackChan 管理アプリ
Exec=$D/run.sh
Icon=$D/stackchan.png
Terminal=false
Categories=Utility;
StartupWMClass=stackchan
DESK
update-desktop-database ~/.local/share/applications 2>/dev/null || true
echo "登録しました: アプリ一覧の「StackChan」から起動できます"
