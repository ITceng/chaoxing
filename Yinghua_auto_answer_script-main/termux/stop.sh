#!/data/data/com.termux/files/usr/bin/bash
# 停止刷课
pkill -f aoaostar_mooc && echo "==> 已停止" || echo "==> 没有在运行的进程"
termux-wake-unlock 2>/dev/null
