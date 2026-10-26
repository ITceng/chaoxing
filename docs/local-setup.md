# 本地运行详细步骤（Windows / macOS / Linux）

> 目标：把项目下载到自己的电脑上，**用网页面板管理多个超星账号**（推荐），或者用命令行跑单个账号。
> 全程不需要 Docker、不需要服务器，本机就能跑。

---

## 0. 先看结论：总共 4 步

| 步骤 | 做什么 | 大概耗时 |
|---|---|---|
| 1 | 装 Python（3.11 ~ 3.13） | 5 分钟 |
| 2 | 下载项目（ZIP 或 git clone） | 1 分钟 |
| 3 | 运行一键启动脚本（自动建虚拟环境 + 装依赖） | 首次 2~5 分钟 |
| 4 | 浏览器打开 `http://127.0.0.1:8080` → 创建管理员 → 添加账号 → 启动 | 2 分钟 |

---

## 1. 安装 Python

### Windows

1. 打开 <https://www.python.org/downloads/windows/>，下载 **Python 3.13.x（64-bit installer）**。
2. 双击安装，**第一屏底部的 `Add python.exe to PATH` 必须勾上**，然后点 `Install Now`。
3. 装完按 `Win + R`，输入 `cmd`，在窗口里执行：

```bat
python --version
```

能看到 `Python 3.13.x` 就成功了。
如果提示"不是内部或外部命令"，说明 PATH 没配好——重新运行安装包选 `Modify` → 勾选 `Add Python to environment variables`，或直接卸载重装。

> 如果 `python --version` 打开的是微软商店，说明你装的是商店版别名。请到「设置 → 应用 → 高级应用设置 → 应用执行别名」里关掉 `python.exe` / `python3.exe`，或者直接用 `py -3 --version` 验证。

### macOS

```bash
# 方式一：官网安装包
# https://www.python.org/downloads/macos/ 下载 3.13 的 .pkg 双击安装

# 方式二：Homebrew（已装 brew 的话）
brew install python@3.13
```

验证：

```bash
python3 --version
```

### Linux（Ubuntu / Debian）

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 --version
```

> 版本要求：**最低 3.10，推荐 3.12 / 3.13**。项目 `pyproject.toml` 里写的是 `>=3.13`，那是 `pip install .` 时用的；直接从源码运行 3.11 也验证可用。

---

## 2. 下载项目

### 方式 A：下载 ZIP（不用装 git，最适合新手）

浏览器打开下面任意一个地址，会自动下载压缩包：

- 当前开发分支（含多账号面板）：<https://github.com/ITceng/chaoxing/archive/refs/heads/arena/ea1b55c2-chaoxing.zip>
- 或打开仓库页面 → 右上角分支下拉框选分支 → `Code` → `Download ZIP`

下载后解压，例如：

- Windows：`D:\chaoxing\`（解压后应能看到 `panel.py`、`requirements.txt`、`run-panel.bat`）
- macOS / Linux：`~/chaoxing/`

> 建议解压到**纯英文、无空格**的路径（例如 `D:\chaoxing`），能避免极少数依赖在中文路径下的奇怪问题。
> 注意要解压，**不要直接在压缩包里双击运行**。

### 方式 B：git clone（后续更新方便）

```bash
git clone -b arena/ea1b55c2-chaoxing https://github.com/ITceng/chaoxing.git
cd chaoxing
```

---

## 3. 启动面板（推荐方式）

### Windows：双击 `run-panel.bat`

在解压出来的文件夹里双击 **`run-panel.bat`**，它会自动完成：

1. 找到本机 Python；
2. 第一次运行时创建 `.venv` 虚拟环境；
3. 安装 `requirements-panel.txt` 里的依赖（flask、cryptography、requests、ddddocr…）；
4. 启动面板并自动打开浏览器。

第一次可能要等 2~5 分钟（在下载依赖）。看到这样的输出就成功了：

```
[1/2] 第一次运行：正在创建虚拟环境 .venv ...
[2/2] 正在安装依赖（首次约 2-5 分钟，取决于网速）...
面板地址： http://127.0.0.1:8080
 * Running on http://127.0.0.1:8080
```

**不要关闭这个黑色窗口**，它就是面板进程；关掉窗口 = 停止面板。

### macOS / Linux：执行 `run-panel.sh`

```bash
cd ~/chaoxing
chmod +x run-panel.sh      # 第一次需要
./run-panel.sh
```

### 手动启动（想自己控制环境时）

Windows（PowerShell 或 cmd）：

```bat
cd /d D:\chaoxing
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-panel.txt
.venv\Scripts\python.exe panel.py --open
```

macOS / Linux：

```bash
cd ~/chaoxing
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-panel.txt
.venv/bin/python panel.py --open
```

> `--open` 表示启动后自动打开浏览器，不加也行，手动访问 `http://127.0.0.1:8080`。

### 常用启动参数

| 命令 | 说明 |
|---|---|
| `python panel.py` | 默认 `127.0.0.1:8080`，只有本机能访问 |
| `python panel.py --port 8081` | 端口被占用时换一个 |
| `python panel.py --open` | 启动后自动打开浏览器 |
| `python panel.py --host 0.0.0.0` | 允许同一局域网内的手机/其他电脑访问（见第 7 节） |
| `python panel.py --init-admin` | 在命令行里创建管理员（不想用网页初始化时） |
| `python panel.py --debug` | 调试模式（正式用别开） |

---

## 4. 第一次使用面板

### 4.1 创建管理员

浏览器打开 <http://127.0.0.1:8080>，首次会显示「初始化管理员」：

- 用户名：任意，3~64 位；
- 密码：**至少 10 位**（比如 `My-Panel-2026!`）；
- 创建后自动登录。

> 密码忘了怎么办：关闭面板，删除 `data/admin.json`，重启后会重新进入初始化页面（已添加的账号数据不会丢）。

### 4.2 添加第一个账号

点右上角 **「添加账号」**，填：

| 字段 | 说明 |
|---|---|
| 账号名称 | 自己看的备注，如"我的号" |
| 用户名 / 密码 | 超星手机号 + 密码（加密存储，接口只显示掩码） |
| 使用 Cookie 登录 | 只有在你密码登录被风控时才勾选，并把浏览器里的 Cookie 粘到下面 |
| 课程列表 | **留空或填 `*` = 全部课程**；填课程 ID 用英文逗号分隔，填错会直接报错而不是偷偷跑全部 |
| 倍速 | 1~2，建议 1~1.5 |
| 并发章节 | 1~8，新手用 2 |
| 未开放章节 | `retry`（默认，自动重试）/ `ask` / `continue`（跳过） |
| 课程内章节串行 | **保持勾选**（推荐，保证章节解锁顺序） |
| 无人值守 | 勾选后，面板启动时自动开始这个账号、失败自动重启 |

保存后账号卡片出现，点 **「启动」** 开始。

### 4.3 （可选）配置题库

左侧/顶部 **「设置」** → 题库：

- `provider`：填题库名（例如 `TikuYanxi`），必须是下拉列表里的名字，大小写一致；
- `tokens` / `key`：按题库要求填；
- 提交模式：`false` = 只答题不提交（安全），`true` = 覆盖率达到 `cover_rate` 后自动提交；
- 保存后，账号运行时就会用这份配置。**正在运行的账号需要停止再启动才会生效。**

> 有章节检测、任务点需要解锁的课程，必须配题库，否则会卡住。

### 4.4 看进度 / 控制

- 卡片上能看到：状态（运行中/暂停/失败/已完成）、进度条、成功/失败/跳过数量、当前章节、耗时；
- 按钮：**暂停 / 继续 / 停止 / 日志 / 编辑 / 删除**；
- **停止或暂停只影响这一个账号**，其他账号继续跑；
- 「日志」里能看到该账号的实时输出（敏感信息已脱敏）。

### 4.5 停止面板

- 关掉运行脚本的窗口，或在该窗口按 `Ctrl + C`；
- 下次再双击 `run-panel.bat` / 执行 `./run-panel.sh` 即可，账号配置都在，不用重配。

---

## 5. 另一种用法：命令行跑单账号

如果你只想用命令行、不想要面板，也可以：

```bash
# 1. 复制配置模板
#    Windows: copy config_template.ini config.ini
cp config_template.ini config.ini

# 2. 编辑 config.ini，填 [common] 里的 username / password / course_list

# 3. 运行
.venv/bin/python main.py -c config.ini        # macOS/Linux
.venv\Scripts\python.exe main.py -c config.ini  # Windows
```

或者完全用命令行参数：

```bash
python main.py -u 手机号 -p 密码 -l 课程ID1,课程ID2
python main.py -u 手机号 -p 密码                # 交互式选择课程
python main.py -u 手机号 -p 密码 -l "*"         # 全部课程
```

常用参数：

| 参数 | 说明 |
|---|---|
| `-c config.ini` | 用配置文件 |
| `-u` / `-p` | 账号 / 密码 |
| `-l` | 课程 ID，逗号分隔；`*` 表示全部 |
| `-s 1.5` | 倍速（最大 2） |
| `-j 2` | 并发章节数 |
| `-a continue` | 未开放章节直接跳过 |
| `--course-parallel` | 课程内章节也并发（旧行为，可能打乱章节顺序） |
| `-lc -tc 100` | 增加章节学习次数，目标 100 次 |
| `-v` | 输出调试日志 |

退出码：`0` 全部成功 / `1` 有章节失败或运行出错 / `2` 参数或课程列表错误（含被主动停止） / `130` 被 Ctrl+C 中断。
适合写脚本/计划任务时判断结果。

> 面板和命令行**不要同时跑同一个账号**，会互相顶掉登录。

---

## 6. 数据存在哪里 / 怎么备份

面板模式下所有数据都在项目目录的 `data/`：

```text
data/
├── admin.json        # 管理员账号（scrypt 哈希）
├── accounts.json     # 超星账号（账号密码/Cookie 加密后的密文）
├── secret.key        # 加密密钥（0600 权限）★
├── settings.enc      # 题库/通知设置（整体加密）
├── session.key       # 会话签名密钥
├── audit.log         # 操作审计
├── accounts/<id>/cookies.txt
└── logs/<id>.log
```

- **备份**：整个 `data/` 目录复制走即可；**一定要带上 `secret.key`**，否则密文解不开。
- **换电脑**：把整个项目目录（或至少 `data/`）拷过去，装好依赖再启动。
- **重置管理员**：删 `data/admin.json`。
- **完全清空**：删整个 `data/`（账号全部丢失，谨慎）。
- 这些文件不应上传到网盘公开分享/提交到 Git（仓库的 `.gitignore` 已忽略 `data/`）。

---

## 7. 想用手机/另一台电脑访问本机的面板

默认只监听 `127.0.0.1`，**外网和局域网都访问不到**。同一 Wi-Fi 下临时用：

```bash
# 1) 以局域网模式启动（Windows 下在 run-panel.bat 同目录打开 cmd）
.venv\Scripts\python.exe panel.py --host 0.0.0.0
# macOS/Linux
.venv/bin/python panel.py --host 0.0.0.0
```

```bash
# 2) 查本机局域网 IP
Windows:  ipconfig        → 看 "IPv4 地址"，例如 192.168.1.20
macOS:    ifconfig | grep "inet "
Linux:    hostname -I
```

手机浏览器访问 `http://192.168.1.20:8080`。

⚠️ 注意事项：

- 这是**明文 HTTP**，只适合家里的可信网络；不要在咖啡厅/公司公共网这么用；
- Windows 第一次会弹防火墙提示，选「允许专用网络」；
- 用完改回默认（不带 `--host`）即可；
- 要在公网用手机管理，请按 [`docs/mobile-control.md`](mobile-control.md) 配 HTTPS 反向代理，不要直接把 8080 端口映射到公网。

---

## 8. 常见问题

**Q1：`pip install` 很慢 / 卡住 / 超时**
换国内镜像（清华）：

```bash
# Windows
.venv\Scripts\python.exe -m pip install -r requirements-panel.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# macOS/Linux
.venv/bin/python -m pip install -r requirements-panel.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**Q2：`No module named flask/cryptography`**
说明依赖没装到当前解释器。不要用系统 Python 直接跑，用 `.venv` 里的 Python，或重新运行 `run-panel.bat` / `run-panel.sh`。

**Q3：`OSError: [WinError 10048] 通常每个套接字地址只允许使用一次`（端口被占用）**

```bash
python panel.py --port 8081
```

（macOS/Linux 上对应的报错是 `Address already in use`。）

**Q4：Windows 装依赖时报 `Microsoft Visual C++ 14.0 is required`**
先升级 pip 再装（新版本都有预编译轮子）：

```bat
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-panel.txt
```

仍失败时，装一下微软的 [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)。

**Q5：`run-panel.bat` 一闪而过**
在文件夹地址栏输入 `cmd` 回车，然后手动执行 `run-panel.bat`，就能看到完整报错（脚本本身最后也会 `pause`）。

**Q6：面板能打开但账号一直"失败"**
看该账号「日志」：

- `登录失败` / 需要验证码 → 密码错了或被风控，先在浏览器登录一次超星，或改用 Cookie 登录；
- `指定的课程 ID 在账号下不存在` → 课程列表填错了，留空或填 `*`；
- `SSLError` / `ConnectionError` → 网络或代理问题，检查是否能正常访问超星网页；
- 题库相关报错 → 检查 provider 名称、token、余额。

**Q7：`data/` 里的 `secret.key` 被我删了**
`accounts.json` 里的密码/Cookie 就无法解密了，只能删掉账号重新添加。所以备份时务必连 `secret.key` 一起备份。

**Q8：怎么更新到最新代码？**
ZIP 方式：重新下载解压覆盖（保留 `data/` 目录）；git 方式：

```bash
git pull
.venv/bin/python -m pip install -r requirements-panel.txt   # 依赖有更新时
```

**Q9：能不能关掉"无人值守"自动启动？**
可以。每个账号的编辑弹窗里取消「面板启动后自动开始」；或启动前设置环境变量 `PANEL_AUTOSTART=0`（Windows：`set PANEL_AUTOSTART=0` 再启动）。

**Q10：电脑休眠后账号还跑吗？**
面板是普通进程，电脑睡眠/关机就停了。想 7×24 跑请放到服务器或小主机上，见 [`docs/mobile-control.md`](mobile-control.md) 与 [`docs/panel-deployment.md`](panel-deployment.md)。

---

## 9. 一页速查

```bash
# 下载
git clone -b arena/ea1b55c2-chaoxing https://github.com/ITceng/chaoxing.git
cd chaoxing

# 启动（Windows 双击 run-panel.bat；macOS/Linux 用下面这行）
./run-panel.sh

# 手动方式
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-panel.txt
.venv/bin/python panel.py --open

# 打开
http://127.0.0.1:8080
```
