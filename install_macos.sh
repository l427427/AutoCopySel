#!/bin/bash
# AutoCopySel 一键安装（macOS / Apple Silicon）
#
# 用法：在项目目录打开终端，执行
#   ./install_macos.sh
#
# 它会依次完成：打包 → 退出旧实例 → 安装到 /Applications → 重置旧的辅助功能
# 授权记录 → 启动。为什么必须重置：本程序是 ad-hoc 签名，TCC 里存的是 cdhash，
# 重新打包后 cdhash 变了，旧的「已勾选」会失配，界面上亮着但实际被拒。
set -euo pipefail
cd "$(dirname "$0")"

BUNDLE_ID="com.liqin.autocopysel"
APP_SRC="dist/AutoCopySel.app"
APP_DST="/Applications/AutoCopySel.app"

echo "== 1/5 打包 =="
PYTHON="${PYTHON:-python3}" ./build_macos.sh

if [ ! -d "$APP_SRC" ]; then
  echo "打包失败：找不到 $APP_SRC"
  exit 1
fi

echo
echo "== 2/5 退出正在运行的实例 =="
osascript -e 'quit app "AutoCopySel"' >/dev/null 2>&1 || true
pkill -f "AutoCopySel.app/Contents/MacOS/AutoCopySel" >/dev/null 2>&1 || true
sleep 1

echo "== 3/5 安装到 /Applications =="
if [ -d "$APP_DST" ]; then
  TRASH="$HOME/.Trash"
  mkdir -p "$TRASH"
  DEST="$TRASH/AutoCopySel-旧版-$(date +%Y%m%d-%H%M%S).app"
  mv "$APP_DST" "$DEST" 2>/dev/null && echo "旧版本已移到废纸篓：$DEST" \
    || { mv "$APP_DST" "/tmp/AutoCopySel-old.app" && echo "旧版本已移到 /tmp/AutoCopySel-old.app"; }
fi
cp -R "$APP_SRC" /Applications/
xattr -dr com.apple.quarantine "$APP_DST" >/dev/null 2>&1 || true
echo "已安装：$APP_DST"

echo
echo "== 4/5 重置旧的辅助功能授权记录 =="
# 两个权限都要清：只清辅助功能的话，输入监控里的旧记录（对应旧 cdhash）
# 会让系统设置显示「已勾选」但实际被拒。
for SVC in Accessibility ListenEvent; do
  if /usr/bin/tccutil reset "$SVC" "$BUNDLE_ID"; then
    echo "  已重置 $SVC"
  else
    echo "  !! $SVC 重置失败，请手动执行：tccutil reset $SVC $BUNDLE_ID"
  fi
done

echo
echo "== 5/5 启动 =="
open -a "$APP_DST"
sleep 2

echo
echo "======================================================"
echo " 最后一步（只需做一次）："
echo "   系统设置 → 隐私与安全性 → 辅助功能 → 勾选 AutoCopySel"
echo
echo " 勾上之后 2 秒内自动生效，不需要重启程序。"
echo " 菜单栏图标从「红色叹号」变回正常图标就说明成功了。"
echo
echo " 如果勾上后图标没变化，点菜单栏图标 → 「修复权限（重置授权记录）」。"
echo "======================================================"
