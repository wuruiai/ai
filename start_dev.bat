@echo off
rem ============================================
rem 水利 RAG + Agent - 一键启动（Windows）
rem 用法：双击本文件，会开两个终端窗口：
rem   1) 后端 FastAPI    http://127.0.0.1:8001
rem   2) 前端 Vite       http://127.0.0.1:5173
rem 浏览器访问 http://127.0.0.1:5173
rem
rem 启动前会自动释放 8001 / 5173 端口上残留的旧进程
rem （避免上次没关干净导致 "port already in use"）。
rem ============================================
setlocal
cd /d "%~dp0"

rem 确保数据库已初始化
".venv\Scripts\python.exe" -m scripts.init_db >nul 2>&1

rem 释放上次残留的旧进程（端口无占用则什么都不做）
call :kill_port 8001
call :kill_port 5173

rem 启动后端（独立窗口）
start "water-rag-backend" cmd /k ".venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --log-level info"

rem 启动前端（独立窗口）
start "water-rag-frontend" cmd /k "cd /d frontend && npm run dev -- --host 127.0.0.1 --port 5173"

echo.
echo 已启动两个服务：
echo   后端: http://127.0.0.1:8001  (健康检查 /health)
echo   前端: http://127.0.0.1:5173  (浏览器打开这个)
echo.
echo 关闭方式：直接关掉两个弹出的黑色终端窗口。
pause
exit /b

rem ============================================
rem 释放指定端口上的监听进程（没有则什么都不做）
rem 用法：call :kill_port 8001
rem ============================================
:kill_port
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%1 .*LISTENING"') do (
    echo   端口 %1 被旧进程 PID %%p 占用，正在结束...
    taskkill /F /PID %%p >nul 2>&1
)
exit /b
