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

echo 【真实发送】将对沟通列表里含「继续沟通」的会话发送短跟进（默认本轮最多 5 个）。
echo 请先跑过「HR-跟进-试跑.bat」确认文案无误；不要高频连续双击。
echo.
python boss followup --max 5
echo.
pause
