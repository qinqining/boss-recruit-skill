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

echo 将打开浏览器，请用 BOSS 招聘方账号扫码登录。
echo 登录成功后窗口可关闭；本机只需做一次（除非 cookie 失效）。
echo.
py boss login
echo.
pause
