@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================
echo   超星多账号控制面板 - 本地启动（Windows）
echo ==================================================
echo.

rem ---------- 1. 找 Python ----------
set "PYBASECMD="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import sys" >nul 2>nul
    if !errorlevel!==0 set "PYBASECMD=py -3"
)
if not defined PYBASECMD (
    where python >nul 2>nul
    if !errorlevel!==0 (
        python -c "import sys" >nul 2>nul
        if !errorlevel!==0 set "PYBASECMD=python"
    )
)
if not defined PYBASECMD (
    echo [错误] 没有找到可用的 Python。
    echo.
    echo 请先安装 Python 3.11 ~ 3.13：
    echo   https://www.python.org/downloads/
    echo 安装时务必勾选 "Add python.exe to PATH"，装完重新打开这个窗口再试。
    echo.
    pause
    exit /b 1
)

rem ---------- 2. 准备虚拟环境 ----------
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 第一次运行：正在创建虚拟环境 .venv ...
    %PYBASECMD% -m venv .venv
    if errorlevel 1 goto :fail

    echo [2/2] 正在安装依赖（首次约 2-5 分钟，取决于网速）...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    ".venv\Scripts\python.exe" -m pip install -r requirements-panel.txt
    if errorlevel 1 (
        echo.
        echo 提示：如果卡在下载或超时，可以换国内镜像重试：
        echo   .venv\Scripts\python.exe -m pip install -r requirements-panel.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        goto :fail
    )
) else (
    echo [1/2] 已存在虚拟环境 .venv，跳过安装。
    echo [2/2] 如需更新依赖：.venv\Scripts\python.exe -m pip install -r requirements-panel.txt
)

rem ---------- 3. 启动 ----------
echo.
echo 面板地址： http://127.0.0.1:8080  （如需换端口：run-panel.bat --port 8081）
echo 首次打开需要创建管理员账号（密码至少 10 位）。
echo 关闭本窗口或按 Ctrl+C 即可停止面板。
echo.
".venv\Scripts\python.exe" panel.py --open %*

echo.
echo 面板已退出。
pause
exit /b 0

:fail
echo.
echo [失败] 启动失败，请把上面的错误信息复制出来排查。
echo 常见原因：Python 版本过低、网络无法访问 PyPI、依赖安装被安全软件拦截。
pause
exit /b 1
