@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 python。请先安装 Python 3，安装时勾选「Add python.exe to PATH」。
  pause
  exit /b 1
)

echo 【试跑】只打印将要发送的文案，不会真的发出去。
echo 请确认已与「打招呼」使用同一台电脑、且已用 HR-登录.bat 登录过。
echo 建议先让管理员在 config.json 里写好公司地址/通勤说明（followup 段）。
echo.
python boss followup --dry-run --max 3
echo.
pause
