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

echo 【真实发送】将对沟通列表里含「继续沟通」的会话发送短跟进（默认本轮最多 5 个）。
echo 请先跑过「HR-跟进-试跑.bat」确认文案无误；不要高频连续双击。
echo.
py boss followup --max 5
echo.
pause
