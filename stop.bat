@echo off
chcp 936 >nul 2>&1
title FileStudio 停止脚本
echo ============================================
echo   正在停止 FileStudio 服务（端口 1111）
echo ============================================
set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":1111" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%P >nul 2>&1
    if not errorlevel 1 (
        echo 已结束进程 PID %%P
        set "KILLED=1"
    )
)
if "%KILLED%"=="0" (
    echo 未发现监听 1111 端口的进程（服务可能未运行）。
) else (
    echo 服务已停止。
)
echo 按任意键退出...
pause >nul
