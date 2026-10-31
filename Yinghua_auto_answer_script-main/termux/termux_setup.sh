#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
# aoaostar_mooc 刷课工具 - Termux(安卓) 一键部署脚本
# 用法: bash termux_setup.sh
# 可选: SRC=/path/to/mooc-main bash termux_setup.sh  指定源码目录
# ============================================================
set -e

SRC="${SRC:-}"
DEPLOY_DIR="$HOME/mooc"
ARCH="$(uname -m)"

echo "==> 当前架构: $ARCH"
case "$ARCH" in
    aarch64|armv8*) ;;
    armv7|armv8l) echo "[!] 32位系统, 编译可用但性能较差" ;;
    *) echo "[!] 非ARM架构($ARCH), 手机上通常不会出现";;
esac

# ---------- 0. 存储权限(用于从手机下载目录找源码) ----------
if [ ! -d /sdcard/Download ]; then
    echo "==> 申请存储权限, 请在弹窗中允许"
    termux-setup-storage || true
    sleep 2
fi

# ---------- 1. 安装依赖 ----------
echo "==> [1/5] 安装 golang / git ..."
pkg install -y golang git
# 国内网络必须换 Go 模块代理, 否则拉取依赖会卡死
go env -w GOPROXY=https://goproxy.cn,direct

# ---------- 2. 定位源码 ----------
echo "==> [2/5] 查找源码 ..."
if [ -z "$SRC" ]; then
    for cand in \
        "$HOME/mooc-main" \
        "$HOME/mooc-main/mooc-main" \
        "$HOME/storage/downloads/mooc-main" \
        "$HOME/storage/downloads/mooc-main/mooc-main" \
        "/sdcard/Download/mooc-main" \
        "/sdcard/Download/mooc-main/mooc-main" \
        "/sdcard/Download/mooc-src/mooc-main"; do
        if [ -f "$cand/main.go" ]; then SRC="$cand"; break; fi
    done
fi

if [ -z "$SRC" ]; then
    echo "[!] 未找到本地源码, 尝试从 GitHub 克隆 (网络不通请把PC上的 mooc-main 传到手机 Download 目录后重跑本脚本)"
    rm -rf "$HOME/mooc-src"
    git clone --depth 1 https://github.com/aoaostar/mooc.git "$HOME/mooc-src"
    SRC="$HOME/mooc-src"
fi
echo "    源码目录: $SRC"
[ -f "$SRC/main.go" ] || { echo "[x] 源码无效(找不到 main.go)"; exit 1; }

# ---------- 3. 应用多账号健壮性补丁 ----------
# 原版: 任何一个账号登录失败会 logrus.Fatal 杀掉整个程序
# 补丁: 改为记录错误并跳过该账号/任务 (仅限 send/work 函数体内, 对已修补源码无副作用)
echo "==> [3/5] 应用补丁 (单账号失败不终止全局) ..."
sed -i '/^func send/,/^}/ s/^\t\tlogrus\.Fatal(err)$/\t\tlogrus.Errorf("账号登录\/取课失败, 已跳过: %s", err)\n\t\treturn/' \
    "$SRC/bootstrap/run.go" 2>/dev/null || true
sed -i '/^func work/,/^}/ s/^\t\tlogrus\.Fatal(err)$/\t\tlogrus.Errorf("任务执行失败, 已跳过: %s", err)\n\t\treturn/' \
    "$SRC/pkg/task/task.go" 2>/dev/null || true

# ---------- 4. 编译 (纯Go, 静态编译, 无需CGO) ----------
echo "==> [4/5] 编译中, 手机上约需 1-3 分钟 ..."
mkdir -p "$DEPLOY_DIR/logs"
cd "$SRC"
CGO_ENABLED=0 go build -trimpath -o "$DEPLOY_DIR/aoaostar_mooc" main.go

# 部署 web 面板与配置
rm -rf "$DEPLOY_DIR/view"
cp -r "$SRC/view" "$DEPLOY_DIR/"
if [ ! -f "$DEPLOY_DIR/config.json" ]; then
    cp "$SRC/sample.config.json" "$DEPLOY_DIR/config.json"
    echo "    已生成默认配置: $DEPLOY_DIR/config.json (请编辑填入账号)"
else
    echo "    保留已有配置: $DEPLOY_DIR/config.json"
fi

# ---------- 5. 完成 ----------
echo ""
echo "=============================================="
echo " 部署完成! 目录: $DEPLOY_DIR"
echo " 下一步:"
echo "   1. nano $DEPLOY_DIR/config.json   填写账号(users可填多个)"
echo "   2. bash run.sh                    开始刷课"
echo "   3. 手机浏览器打开 http://127.0.0.1:10086 看进度"
echo "=============================================="
