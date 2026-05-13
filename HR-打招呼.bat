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

echo 当前目录: %CD%
echo 正在执行：推荐牛人筛选并打招呼（默认规则与上限见项目说明）。
echo 若从未在本机登录过，请先双击运行「HR-登录.bat」。
echo 过程中请勿操作鼠标键盘，直至本窗口提示结束。
echo.
python boss greet
echo.
echo ----- 运行结束。若报错或页面要求验证，请截图发给管理员 -----
pause
