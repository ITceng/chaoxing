@echo off
chcp 65001 >nul
title aoaostar_mooc 多平台版编译器
REM ============================================================
REM  一键编译多平台版 aoaostar_mooc.exe (英华+学习通+知到)
REM  前置: 需安装 Go 1.17+  →  winget install GoLang.Go
REM ============================================================

set "SRC=D:\Downloads\mooc-main\mooc-main"
set "OUT=D:\Downloads\aoaostar_mooc_v1.3.2_windows_amd64\aoaostar_mooc_windows_amd64"

echo [1/3] 设置国内 Go 模块代理并拉取依赖...
cd /d "%SRC%"
go env -w GOPROXY=https://goproxy.cn,direct
go mod tidy
if errorlevel 1 (
    echo 依赖拉取失败, 请检查网络
    pause
    exit /b 1
)

echo [2/3] 编译中...
go build -trimpath -o "%OUT%\aoaostar_mooc_new.exe" main.go
if errorlevel 1 (
    echo 编译失败, 请检查上方报错
    pause
    exit /b 1
)

echo [3/3] 替换运行目录中的 exe...
move /y "%OUT%\aoaostar_mooc_new.exe" "%OUT%\aoaostar_mooc.exe" >nul
echo.
echo ============================================================
echo  完成! 新版 aoaostar_mooc.exe 已就绪
echo  (原版备份: aoaostar_mooc_v1.3.2_原版备份.exe)
echo ============================================================
pause
