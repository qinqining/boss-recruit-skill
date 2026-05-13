@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 py。请安装 Python 3（Windows 安装程序勾选「py launcher」），或将 py 加入 PATH。
  pause
  exit /b 1
)

echo 【试跑】只打印将要发送的文案，不会真的发出去。
echo 请确认已与「打招呼」使用同一台电脑、且已用 HR-登录.bat 登录过。
echo 建议先让管理员在 config.json 里写好公司地址/通勤说明（followup 段）。
echo.
py boss followup --dry-run --max 3
echo.
pause
