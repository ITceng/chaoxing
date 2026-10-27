#!/usr/bin/env bash
# 超星多账号控制面板 - 本地启动（macOS / Linux）
# 用法：./run-panel.sh            默认 127.0.0.1:8080 并自动打开浏览器
#       ./run-panel.sh --port 8081 --host 0.0.0.0
#       PYTHON_BIN=/usr/bin/python3.12 ./run-panel.sh   # 指定解释器（跳过 .venv）
set -euo pipefail
cd "$(dirname "$0")"

echo "=================================================="
echo "  超星多账号控制面板 - 本地启动"
echo "=================================================="
echo

if [ -n "${PYTHON_BIN:-}" ]; then
  PY_RUN="$PYTHON_BIN"
  echo "[1/2] 使用指定解释器：$PY_RUN"
elif [ -x ".venv/bin/python" ]; then
  PY_RUN=".venv/bin/python"
  echo "[1/2] 已存在虚拟环境 .venv，跳过安装。"
  echo "      如需更新依赖：.venv/bin/python -m pip install -r requirements-panel.txt"
else
  BASE_PY="${PYTHON_BIN:-python3}"
  if ! command -v "$BASE_PY" >/dev/null 2>&1; then
    echo "[错误] 没有找到 python3，请先安装 Python 3.11 ~ 3.13。"
    echo "  macOS:  brew install python@3.13"
    echo "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
    exit 1
  fi
  echo "[1/2] 第一次运行：创建虚拟环境 .venv ..."
  "$BASE_PY" -m venv .venv
  echo "[2/2] 安装依赖（首次约 1-5 分钟）..."
  .venv/bin/python -m pip install --upgrade pip --quiet
  if ! .venv/bin/python -m pip install -r requirements-panel.txt; then
    echo
    echo "提示：下载失败可换国内镜像重试："
    echo "  .venv/bin/python -m pip install -r requirements-panel.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 1
  fi
  PY_RUN=".venv/bin/python"
fi

SHOW_PORT=8080
SHOW_HOST=127.0.0.1
prev=""
for arg in "$@"; do
  case "$prev" in
    --port) SHOW_PORT="$arg" ;;
    --host) SHOW_HOST="$arg" ;;
  esac
  prev="$arg"
done

echo
echo "面板地址： http://${SHOW_HOST/0.0.0.0/127.0.0.1}:${SHOW_PORT}"
echo "首次打开需要创建管理员账号（密码至少 10 位）。"
echo "按 Ctrl+C 停止面板；忘记密码执行：./run-panel.sh --reset-admin"
echo
OPEN_FLAG=--open
for arg in "$@"; do
  case "$arg" in
    --reset-admin|--init-admin) OPEN_FLAG="" ;;   # 这两个是管理命令，启动完就退出
  esac
done

exec "$PY_RUN" panel.py $OPEN_FLAG "$@"
