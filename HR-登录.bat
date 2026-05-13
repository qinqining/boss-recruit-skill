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

echo 将打开浏览器，请用 BOSS 招聘方账号扫码登录。
echo 登录成功后窗口可关闭；本机只需做一次（除非 cookie 失效）。
echo.
python boss login
echo.
pause
