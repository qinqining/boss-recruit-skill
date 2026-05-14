@echo off
REM 备份副本：与 C:\Users\HP\.claude\启动Claude Code.bat 相同逻辑。
REM 若 .claude 下文件丢失，可复制本文件到 C:\Users\<用户名>\.claude\ 后使用；或改下面 cd 为常用项目目录。
setlocal EnableExtensions
chcp 65001 >nul
title Claude Code CLI
cd /d "%USERPROFILE%"

echo [信息] 当前目录: %CD%
echo [信息] Claude 配置目录: %USERPROFILE%\.claude
echo.

where claude >nul 2>nul
if errorlevel 1 (
    echo [错误] 未在 PATH 中找到 claude 命令。
    echo 请安装 Claude Code CLI，CMD 中执行: claude --version
    goto :END
)

echo [启动] Claude Code…
echo.
claude

:END
echo.
echo ---------- 进程已结束 ----------
pause
endlocal
exit /b 0
