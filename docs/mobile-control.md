# 超星控制面板：手机远程管理 & 无人值守指南

> 一句话回答：**可以。** 面板常驻在你的服务器上，账号由面板自己启动、失败自动重启；
> 你用手机浏览器（或手机上的 API 脚本）连上同一个面板，就能单独停止/暂停/继续某个账号，
> 其他账号照常运行。

---

## 1. 它是怎么"自动跑"的

三层保障，缺一不可：

| 层 | 作用 | 怎么配 |
|---|---|---|
| **容器/服务层** | 机器重启、进程崩溃后把面板重新拉起来 | `docker compose` 的 `restart: unless-stopped`，或 systemd `Restart=on-failure` |
| **面板自动启动账号** | 面板起来后自动开始 `autostart` 的账号 | 面板设置里每个账号的「无人值守」开关，默认开启 |
| **失败自动重启** | 某账号任务失败（网络/题库/风控）后自动重试 | 账号的「失败后自动重启」开关，默认开启（最多 5 次/小时，退避 20s→40s→…→600s） |

关键规则：

- **手动停止的账号不会被自动重启**。你在手机上点「停止」= 明确意图，面板不会跟你对着干；
  之后点「启动」才恢复自动管理。
- **暂停不是停止**：暂停只是阻塞该账号的检查点，进程/会话都还在，点「继续」立刻接着跑。
- 自动重启有上限：默认 1 小时内最多 5 次，超过后账号停在「失败」并提示你查看日志，
  避免账号密码错误/被风控时无限重试把账号打死。
- 服务器重启后面板会重新读 `data/accounts.json`，`autostart` 的账号自动开始。

对应环境变量：

```bash
PANEL_AUTOSTART=1              # 1=开启无人值守调度（默认），0=只手动控制
PANEL_SUPERVISOR_INTERVAL=5    # 监督线程检查间隔（秒）
PANEL_MAX_AUTO_RESTARTS=5      # 每账号每滚动 1 小时最多自动重启次数
PANEL_RESTART_BACKOFF=20       # 首次重启等待秒数，之后指数退避，上限 600
```

---

## 2. 手机端怎么用

### 2.1 直接手机浏览器（最快）

1. 服务器上启动面板（见第 3 节），确保 `PANEL_HOST=0.0.0.0` 或前面有反向代理。
2. 手机浏览器打开 `https://panel.你的域名`（**强烈建议 HTTPS**；iOS Safari 对
   纯 HTTP + 登录表单会提示不安全）。
3. 用管理员账号登录 → 账号卡片上直接有「暂停 / 停止 / 继续 / 启动 / 日志 / 编辑 / 删除」。
4. 界面已做手机适配：按钮加大到 44px 点击区、弹窗全屏、输入框 16px（避免 iOS 自动放大）、
   日志面板可滚动、顶部操作按钮铺满一行。

> 小技巧：在手机上「添加到主屏幕」，会像 App 一样全屏打开，不用每次输网址。

### 2.2 手机用 API 控制（脚本 / 快捷指令 / 自动化）

如果你不想开网页，可以用 API Key 直接调接口：

```bash
# 手机上的 Shortcuts/Termux/任意脚本
curl -s -H "X-API-Key: $PANEL_API_KEY" https://panel.example.com/api/accounts | jq '.accounts[] | {name, status: .runtime.status}'

# 停止某个账号（把 <id> 换成 /api/accounts 返回的 id）
curl -s -X POST -H "X-API-Key: $PANEL_API_KEY" https://panel.example.com/api/accounts/<id>/stop

# 继续
curl -s -X POST -H "X-API-Key: $PANEL_API_KEY" https://panel.example.com/api/accounts/<id>/resume
```

API Key 是全权限密钥，**不要写进手机备忘录/公开仓库**；泄露后重启面板并换 key 即可立刻失效。

### 2.3 通知推送到手机

面板设置里配置通知（ServerChan / Bark / Telegram / Qmsg），账号任务结束会推送：

```
chaoxing : 账号 张三 任务结束，成功 42，失败 0，跳过 3
```

Bark/Telegram 在手机上几乎是实时的，适合"跑完叫我"或"失败了叫我"。

---

## 3. 服务器上怎么让它常驻

### 3.1 Docker Compose（推荐，自带自动重启）

```bash
cd /opt/chaoxing
export PANEL_API_KEY="$(openssl rand -hex 32)"
export PANEL_SESSION_SECRET="$(openssl rand -base64 48)"
docker compose -f docker-compose.panel.yml up -d --build

# 看是否活着
docker compose -f docker-compose.panel.yml ps
curl -s http://127.0.0.1:8080/api/health
```

compose 里已经设置：`restart: unless-stopped`、`PANEL_AUTOSTART=1`、非 root 用户、
`no-new-privileges`、数据持久化到 `chaoxing-panel-data` 卷。

> ⚠️ 必须 `--workers 1`（compose 已处理）。账号运行状态在进程内存里，多 worker 会重复启动账号。

### 3.2 systemd（裸机 / 已有 Python 环境）

仓库里提供了 `deploy/chaoxing-panel.service`：

```bash
sudo cp deploy/chaoxing-panel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chaoxing-panel
sudo systemctl status chaoxing-panel
journalctl -u chaoxing-panel -f
```

`Restart=on-failure` + `RestartSec=5`：进程崩溃 5 秒后自动拉起，账号随之按 `autostart` 重新开始。

### 3.3 前面加 Nginx + HTTPS

手机在外面用，**一定要 HTTPS**，否则登录 Cookie/密码是明文过网络。示例：

```nginx
server {
    listen 443 ssl http2;
    server_name panel.example.com;
    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;

    # 只允许自己的常用出口 IP（可选，最有效的防爆破）
    # allow 1.2.3.4; deny all;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

面板侧对应：

```bash
PANEL_COOKIE_SECURE=1     # Cookie 只走 HTTPS
PANEL_TRUST_PROXY=1       # 让限流/审计拿到真实客户端 IP
PANEL_ALLOWED_ORIGINS=https://panel.example.com
```

---

## 4. 手机上操作的安全注意

1. **别用公共 Wi-Fi 直接访问 HTTP**；没有 HTTPS 时，密码和会话都可能被截获。
2. 面板登录有失败锁定（默认 5 次/15 分钟）与限流，但**建议再加一层**：
   反向代理 IP 白名单、VPN、或 Cloudflare Access。
3. 手机丢失时：换一台设备登录 → 「退出全部会话」（`/api/auth/logout-all`）→ 改密码；
   或直接换掉 `PANEL_SESSION_SECRET` 重启面板。
4. 不要把面板地址 + 密码发给别人；`PANEL_API_KEY` 等同管理员口令。
5. 面板默认禁止被搜索引擎收录（`noindex`），并带 CSP / `frame-ancestors` 防点击劫持。

---

## 5. 常见场景对照

| 你的场景 | 操作 | 结果 |
|---|---|---|
| 某个账号要停一下，其他继续 | 手机上点该账号「暂停」 | 该账号阻塞在安全点；其他账号不受影响 |
| 这个账号今天不跑了 | 点「停止」 | 该账号退出，且**不会被自动重启**；其他继续 |
| 停止的账号明天继续 | 点「启动」 | 恢复运行，重新纳入自动管理 |
| 账号失败想让它自己重试 | 保持「失败后自动重启」开启 | 指数退避自动重启，5 次/小时后转人工 |
| 服务器重启了 | 什么都不用做 | 容器/systemd 拉起面板 → autostart 账号自动开始 |
| 想彻底禁用无人值守 | `PANEL_AUTOSTART=0` 或关账号的 autostart | 只在你点「启动」时跑 |
| 想一次停掉所有 | 顶部「停止全部」 | 所有运行中账号停止，且都标记为手动停止 |
| 想临时让所有账号歇一下 | 顶部「暂停全部」 | 全部暂停；点「继续全部」恢复 |

---

## 6. 排查

**问：面板重启后账号没自动开始？**
- 检查该账号的「无人值守（autostart）」是否开启；
- 看 `/api/meta` 的 `supervisor` 是否为 `true`；
- 看日志有没有 `无人值守已开启`。

**问：账号一直失败、不停重启？**
- 到达 5 次/小时上限后会停止自动重启并显示「自动重启已达上限」；
- 看该账号日志（手机也能看）：登录失败、题库 token、Cookie 过期是最常见原因。

**问：管理员密码忘了，手机上登录不了？**
在服务器上执行（不需要先登录面板）：

```bash
cd /opt/chaoxing
PANEL_DATA_DIR=/opt/chaoxing/data .venv/bin/python panel.py --reset-admin
```

新密码**立即生效**，旧密码失效、所有已登录的手机/电脑退回登录页，连续输错造成的锁定也会被清除。
Windows/macOS 本机、Docker、用户名也忘了等情况，见 [`docs/local-setup.md`](local-setup.md) 的 4.6。

**问：手机上点停止没反应？**
- 若状态是「停止中」，说明正在等当前 HTTP 请求返回（最长 15 秒超时）；
- 若接口返回 401/403，是会话过期或 CSRF 失效，重新登录即可。

**问：外网访问不了？**
- 面板默认只监听 `127.0.0.1`，需要 `PANEL_HOST=0.0.0.0`（Docker 已设置）；
- 检查服务器防火墙/云安全组是否放行 443；
- 反向代理要设置 `PANEL_TRUST_PROXY=1`，否则限流会把所有请求当成代理 IP。
