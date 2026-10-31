#!/data/data/com.termux/files/usr/bin/bash
# 启动刷课 (前台运行, 可直接看日志; Ctrl+C 停止)
cd "$HOME/mooc" || { echo "[x] 未部署, 请先运行 bash termux_setup.sh"; exit 1; }

# 防止手机熄屏后进程被杀
termux-wake-lock 2>/dev/null

echo "==> 启动刷课, 面板地址: http://127.0.0.1:10086"
echo "==> 后台运行方式: nohup ./aoaostar_mooc >/dev/null 2>&1 &"
./aoaostar_mooc
