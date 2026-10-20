@echo off
chcp 65001 >nul
REM 在 Windows 上打包成单个 exe（无控制台窗口）
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

%PY% -m pip install -q -r requirements.txt
%PY% -m pip install -q pyinstaller

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist AutoCopySel.spec del /q AutoCopySel.spec

%PY% -m PyInstaller ^
  --noconfirm --clean --onefile --windowed ^
  --name AutoCopySel ^
  --hidden-import pystray._win32 ^
  --hidden-import pynput.keyboard._win32 ^
  --hidden-import pynput.mouse._win32 ^
  --collect-submodules pystray ^
  run.py

echo.
echo 打包完成：dist\AutoCopySel.exe
echo 双击即可运行，托盘区会出现图标。
echo 如需开机自启：右键托盘图标 - 勾选「开机自启」。
pause
