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
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.1.6" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 11" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleVersion 11" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string AutoCopySel 需要读取选中文本" "$PLIST" 2>/dev/null || true

# 优先用本机「代码签名」证书：
#   ad-hoc（-s -）的授权凭据是二进制哈希 cdhash，每打一次包就变一次，
#   系统里那条旧授权记录会「还亮着但已失效」，必须每次重新重置授权。
#   用固定证书签名后，designated requirement 绑定的是证书而不是哈希，
#   重新打包后 TCC 记录依然有效，不用再反复授权。
SIGN_ID="${AUTOCOPYSEL_SIGN_ID:-}"
if [ -z "$SIGN_ID" ] && security find-identity -v -p codesigning 2>/dev/null | grep -q "AutoCopySel Dev"; then
  SIGN_ID="AutoCopySel Dev"
fi

if [ -n "$SIGN_ID" ]; then
  echo "签名身份：$SIGN_ID（固定证书 → 重新打包后授权不会失效）"
  codesign --force --deep --sign "$SIGN_ID" "$APP" 2>/dev/null || true
else
  echo "签名身份：ad-hoc（未找到「AutoCopySel Dev」证书 → 每打一次包都要重新授权一次）"
  codesign --force --deep --sign - "$APP" 2>/dev/null || true
fi

CDHASH=$(codesign -d -vv "$APP" 2>&1 | grep -o 'CDHash=[0-9a-f]*' | head -1 || true)

echo
echo "打包完成：$APP   ${CDHASH}"
echo "校验："
codesign --verify --strict "$APP" && echo "  签名有效"

if [ -n "$SIGN_ID" ]; then
  echo
  echo "安装（用固定证书签名，授权只需做一次）："
  echo "  cp -R \"$APP\" /Applications/ && open /Applications/AutoCopySel.app"
else
  echo
  echo "安装 & 授权（重要）："
  echo "  cp -R \"$APP\" /Applications/"
  echo "  tccutil reset Accessibility com.liqin.autocopysel   # 重建后 cdhash 变了，必须重置旧授权"
  echo "  tccutil reset ListenEvent   com.liqin.autocopysel"
  echo "  open /Applications/AutoCopySel.app"
  echo "  然后在「辅助功能」+「输入监控」里都勾选 AutoCopySel（2 秒内生效，不用重启）"
  echo
  echo "  想彻底摆脱「每次打包都要重新授权」：在钥匙串访问里建一张"
  echo "  「AutoCopySel Dev」代码签名证书，本脚本会自动用它签名。"
fi
