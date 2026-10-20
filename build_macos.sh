#!/bin/bash
# 在 macOS 上打包成 .app（后台程序，无 Dock 图标）
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

"$PY" -m pip install -q -r requirements.txt
"$PY" -m pip install -q pyinstaller

# 旧产物挪到 .trash 而不是直接删（可回滚，也避免误删）
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p .trash
for p in build dist AutoCopySel.spec; do
  if [ -e "$p" ]; then mv "$p" ".trash/${p%.*}-$STAMP"; fi
done

"$PY" -m PyInstaller \
  --noconfirm --windowed \
  --name AutoCopySel \
  --osx-bundle-identifier com.liqin.autocopysel \
  --hidden-import pystray._darwin \
  --hidden-import pynput.keyboard._darwin \
  --hidden-import pynput.mouse._darwin \
  --hidden-import autocopysel.health \
  --hidden-import autocopysel.diagnostics \
  --hidden-import PyObjCTools.AppHelper \
  --collect-submodules pystray \
  run.py

APP="dist/AutoCopySel.app"
PLIST="$APP/Contents/Info.plist"

# 后台常驻：不出现在 Dock 和 Cmd+Tab
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.1.3" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 5" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 5" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string AutoCopySel 需要读取选中文本" "$PLIST" 2>/dev/null || true

# 先签内层再签外层（--deep 已废弃，这里保持 --force 覆盖 PyInstaller 的临时签名）
codesign --force --deep --sign - "$APP" 2>/dev/null || true

CDHASH=$(codesign -d -vv "$APP" 2>&1 | grep -o 'CDHash=[0-9a-f]*' | head -1 || true)

echo
echo "打包完成：$APP   ${CDHASH}"
echo "校验："
codesign --verify --strict "$APP" && echo "  签名有效"
echo
echo "安装 & 授权（重要）："
echo "  cp -R \"$APP\" /Applications/"
echo "  tccutil reset Accessibility com.liqin.autocopysel   # 重建后 cdhash 变了，必须重置旧授权"
echo "  open /Applications/AutoCopySel.app"
echo "  然后在「隐私与安全性 → 辅助功能」里勾选 AutoCopySel（2 秒内自动生效，不用重启）"
