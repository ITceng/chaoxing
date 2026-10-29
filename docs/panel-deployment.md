# 多账号控制面板：部署与安全指南

> 适用版本：`4.0.0`（`panel.py` / `panel_core/`）
> 目标：服务器上管理多个超星账号，**账号之间独立运行**，停止/暂停一个账号不会影响其他账号。

---

## 1. 功能概览

- **多账号管理**：新增、编辑、删除账号；每个账号独立加密保存账号密码/Cookie。
- **独立运行**：每个账号一个运行线程、一个 `RunControl`、一个 `requests.Session`、一个 Cookie 文件、一个日志缓冲。
- **暂停 / 继续 / 停止**：只作用于目标账号；视频循环、重试等待、章节学习次数等耗时操作都会在安全点响应。
- **全局限制**：`max_concurrent_accounts` 控制同时运行的账号数；超出的账号显示“排队中”，停止一个后自动补位。
- **启动面板**：浏览器中查看账号状态、章节进度、当前任务、日志；支持启动全部/暂停全部/停止全部。
- **无人值守**：面板进程/服务器重启后自动开始 `autostart` 账号；账号失败按指数退避自动重启（有上限）；
  手动停止的账号不会被自动拉起。手机浏览器即可远程暂停/停止单个账号，详见
  [`docs/mobile-control.md`](mobile-control.md)。
- **API 安全**：管理员登录 + CSRF + Origin 校验 + 登录限流/锁定 + 可选 API Key + 审计日志。
- **敏感信息脱敏**：日志中的 Cookie / token / 通知 URL 自动脱敏；账号密码、Cookie、题库 token 使用 Fernet 加密落盘。

---

## 2. 快速开始（本机）

```bash
# 1. 安装依赖（面板需要 cryptography；CLI 用户可继续只用 requirements.txt）
pip install -r requirements-panel.txt

# 2. 启动（默认只监听 127.0.0.1:8080）
python panel.py

# 或指定数据目录/监听地址（服务器上通常放在反向代理后面）
PANEL_DATA_DIR=/opt/chaoxing/data PANEL_HOST=127.0.0.1 PANEL_PORT=8080 python panel.py
```

首次打开 `http://127.0.0.1:8080` 会进入“初始化管理员”页面，密码至少 10 位。

也可以用命令行初始化，避免在浏览器里首次提交：

```bash
python panel.py --init-admin
```

初始化完成后，登录面板 → “添加账号” → 填账号密码（或粘贴 Cookie 并勾选 Cookie 登录）→ “启动”。

> 课程列表留空或填 `*` 表示全部课程；填写课程 ID（英文逗号分隔）时，如果 ID 不存在，账号会直接失败，**不会静默变成全部课程**。

---

## 3. 生产部署（服务器 + HTTPS）

### 3.1 推荐拓扑

```text
浏览器 / 脚本
      │ HTTPS (443)
      ▼
Nginx / Caddy / Cloudflare（TLS、限流、IP 白名单）
      │ HTTP (127.0.0.1:8080)
      ▼
panel.py（单进程；账号任务在每个进程内的线程中运行）
      │
      ▼
超星 Web / 题库 / 通知服务
```

面板**不要直接暴露在公网 HTTP**。必须：
1. 只监听 `127.0.0.1`（或容器内网），由反向代理对外；
2. 开启 HTTPS；
3. 设置 `PANEL_COOKIE_SECURE=1`；
4. 设置强随机 `PANEL_API_KEY`（仅在需要脚本调用时）；
5. 在代理层加 IP 白名单/速率限制（面板自身限流只保护单进程）。

### 3.2 Nginx 示例

```nginx
server {
    listen 443 ssl http2;
    server_name panel.example.com;

    ssl_certificate     /etc/letsencrypt/live/panel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/panel.example.com/privkey.pem;

    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

对应环境变量：

```bash
PANEL_HOST=127.0.0.1
PANEL_PORT=8080
PANEL_COOKIE_SECURE=1
PANEL_TRUST_PROXY=1
PANEL_ALLOWED_ORIGINS=https://panel.example.com
PANEL_SESSION_SECRET=<openssl rand -base64 48>
PANEL_SECRET_KEY_FILE=/opt/chaoxing/data/secret.key
```

### 3.3 systemd 示例

```ini
[Unit]
Description=Chaoxing Panel
After=network-online.target

[Service]
Type=simple
User=chaoxing
Group=chaoxing
WorkingDirectory=/opt/chaoxing
Environment=PANEL_DATA_DIR=/opt/chaoxing/data
Environment=PANEL_HOST=127.0.0.1
Environment=PANEL_PORT=8080
Environment=PANEL_COOKIE_SECURE=1
Environment=PANEL_TRUST_PROXY=1
EnvironmentFile=/opt/chaoxing/panel.env
ExecStart=/opt/chaoxing/.venv/bin/gunicorn --workers 1 --threads 8 --timeout 120 \
  --bind 127.0.0.1:8080 panel:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

> **必须 `--workers 1`**。账号运行状态在进程内存中；多 worker 会让同一账号被重复启动，也会让轮询落到不同进程而看不到状态。需要横向扩展时应改用 Redis/数据库做分布式状态，而不是简单加 worker。

### 3.3.1 无人值守相关环境变量

```bash
PANEL_AUTOSTART=1              # 1=开启自动调度（默认），0=只手动控制
PANEL_SUPERVISOR_INTERVAL=5    # 监督线程检查间隔（秒）
PANEL_MAX_AUTO_RESTARTS=5      # 每个账号每滚动 1 小时最多自动重启次数
PANEL_RESTART_BACKOFF=20       # 首次重启等待秒数，之后指数退避，上限 600 秒

# 可选：只接受指定 Host（反代场景建议开启，抵御 Host 头伪造）
PANEL_TRUSTED_HOSTS=panel.example.com
# 可选：把 CSP frame-ancestors 收紧到只允许自己（默认额外允许 Arena 预览域）
PANEL_FRAME_ANCESTORS="'self'"
```

行为约定：

- 面板启动（或收到第一个请求）后，`autostart` 账号自动开始；
- 账号运行失败（登录失败、章节全部失败、异常）→ 自动重启，间隔 20s/40s/80s…；
- 达到上限后账号停在「失败」并提示「自动重启已达上限」，不会无限重试；
- 你手动点「停止」的账号 `user_stopped=True`，**永远**不会被自动重启，直到你手动「启动」。

`deploy/chaoxing-panel.service` 已包含以上配置；Docker Compose 也已透传。

### 3.3.2 管理命令

| 命令 | 用途 |
|---|---|
| `python panel.py` | 启动面板 |
| `python panel.py --open` | 启动并自动打开浏览器（本机使用） |
| `python panel.py --init-admin` | 首次初始化管理员（交互式） |
| `python panel.py --reset-admin` | **忘记密码时重置管理员**（立即生效，可用 `PANEL_ADMIN_USER`/`PANEL_ADMIN_PASSWORD` 免交互） |
| `python panel.py --doctor` | 自检：依赖、数据目录权限、端口占用（并识别端口上是本面板还是别的服务） |

### 3.4 Docker

```bash
# 1. 准备环境变量
export PANEL_API_KEY="$(openssl rand -hex 32)"
export PANEL_SESSION_SECRET="$(openssl rand -base64 48)"
export PANEL_ALLOWED_ORIGINS="https://panel.example.com"

# 2. 启动（只映射到本机，前面放 Nginx）
docker compose -f docker-compose.panel.yml up -d --build

# 3. 日志
docker compose -f docker-compose.panel.yml logs -f panel
```

`/data` 是持久卷，包含加密账号文件和**加密密钥 `secret.key`**。迁移/备份时必须同时备份两者，否则账号密码无法解密。

---

## 4. 多账号隔离与调度

| 资源 | 隔离方式 |
|---|---|
| 运行线程 | 每个账号一个 `AccountRunner.thread` |
| 暂停/停止 | 每个账号一个 `RunControl`；`stop()` 只取消该账号 |
| HTTP 会话 | 每个 worker 线程一个 `requests.Session`（`threading.local`），共享该账号 Cookie 文件 |
| Cookie | `data/accounts/<account_id>/cookies.txt`，权限 0600 |
| 日志 | 日志上下文绑定账号 ID，内存缓冲 + `data/logs/<account_id>.log` |
| 章节调度 | 默认 `course_serial=true`：课程内章节串行、课程之间并行；`jobs` 只控制同一账号内的章节并发 |
| 全局并发 | `max_concurrent_accounts`（默认 2）：超出时排队，不影响已运行账号 |
| 无人值守 | 每个账号 `autostart` / `auto_restart` 开关；手动停止的账号不会被自动重启 |
| 平台 | 每个账号独立选择平台（`platform` 字段）；未实现的平台在面板中不可选 |

行为示例：

```text
账号 A 正在刷课
  → 点击 A 的“停止”：A 的 control.stop() 生效，A 的 worker 在下一个 checkpoint 退出
  → 账号 B 的 control 不受影响，B 继续运行
  → 面板显示 A=已停止，B=运行中
```

注意：
- “暂停”会阻塞当前账号的 checkpoint；正在进行的 HTTP 请求不会被强制中断，但请求结束后会立即停在安全点。
- 停止账号会清空该账号的待执行队列，但**不会删除**已保存的 Cookie/日志/账号配置。
- 修改账号配置在运行中不会热更新，需停止后重新启动（面板会提示）。
- 章节学习次数（每 30 秒一次）同样受暂停/停止控制。

---

## 5. API 与鉴权

### 5.1 两种调用方式

**A. 浏览器会话（推荐给前端/人工操作）**

1. `POST /api/auth/login` 获取会话 Cookie（响应同时返回 `csrf`）；
2. 后续也可用 `GET /api/auth/me` 再取 `csrf`（页面刷新后内存中的 token 会丢失）；
3. 所有写操作带 `X-CSRF-Token: <csrf>`；
4. Cookie 为 `HttpOnly` + `SameSite=Lax`，生产开启 `Secure`。

```bash
curl -c cookies.txt -X POST https://panel.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}'

CSRF=$(curl -s -b cookies.txt https://panel.example.com/api/auth/me | jq -r .csrf)
curl -b cookies.txt -X POST https://panel.example.com/api/accounts/<id>/start \
  -H "X-CSRF-Token: $CSRF"
```

**B. API Key（推荐给脚本/自动化）**

```bash
curl -H "X-API-Key: $PANEL_API_KEY" https://panel.example.com/api/accounts
# 也支持 Authorization: Bearer $PANEL_API_KEY
```

- API Key 只能通过环境变量 `PANEL_API_KEY` 注入，不写入 `data/settings.enc`，不会返回给前端。
- **API Key 拥有完整管理权限**（账号 CRUD、启动/停止、修改设置）。请按管理员密钥对待：
  - 长度至少 32 字节随机；
  - 只保存在服务器/密钥管理系统；
  - 不在浏览器前端使用；
  - 怀疑泄露时立即轮换（重启面板即可生效）。
- 需要更细粒度权限（只读、单账号）时，建议在反向代理层拆分路由，或后续给面板加 RBAC；当前版本没有。

### 5.2 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（无需鉴权，泄露信息极少） |
| GET | `/api/meta` | 版本、题库列表、可选平台列表、`supervisor` 是否在运行（需登录） |
| GET | `/api/auth/status` | 是否已初始化/已登录 |
| POST | `/api/auth/setup` | 首次初始化管理员；可用 `PANEL_SETUP_TOKEN` 保护 |
| POST | `/api/auth/login` | 登录（限流 + 失败锁定） |
| POST | `/api/auth/logout` | 退出当前会话 |
| POST | `/api/auth/logout-all` | 使所有会话失效 |
| GET | `/api/auth/me` | 当前用户与 CSRF token |
| POST | `/api/auth/password` | 修改密码 |
| GET | `/api/accounts` | 账号列表 + 实时状态 |
| GET | `/api/accounts/<id>` | 单个账号详情 |
| POST | `/api/accounts` | 新增账号 |
| PATCH | `/api/accounts/<id>` | 修改账号 |
| DELETE | `/api/accounts/<id>` | 删除账号（停止后删除本地数据） |
| POST | `/api/accounts/<id>/start` | 启动 |
| POST | `/api/accounts/<id>/pause` | 暂停 |
| POST | `/api/accounts/<id>/resume` | 继续 |
| POST | `/api/accounts/<id>/stop` | 停止（不影响其他账号） |
| GET | `/api/accounts/<id>/logs?after=&limit=` | 增量日志 |
| DELETE | `/api/accounts/<id>/logs` | 清空日志 |
| POST | `/api/actions/start-all` | 启动全部（受全局并发限制） |
| POST | `/api/actions/pause-all` | 暂停全部运行中的账号 |
| POST | `/api/actions/stop-all` | 停止全部运行中的账号 |
| POST | `/api/actions/resume-all` | 继续全部暂停中的账号 |
| GET | `/api/settings` | 面板/题库/通知设置（敏感字段返回 `***`） |
| PUT | `/api/settings` | 更新设置；`***` 不会覆盖真实密钥 |
| GET | `/api/audit?limit=` | 审计日志 |

所有 API 写操作（除 setup/login）都要求：
- 浏览器会话：Origin 校验 + `X-CSRF-Token`；
- 或合法的 `X-API-Key` / Bearer token。

---

## 6. 安全模型与已知边界

### 6.1 已实现

| 威胁 | 措施 |
|---|---|
| 密码泄露 | scrypt 哈希（随机 salt），不存明文管理员密码 |
| 账号凭据泄露 | Fernet 加密落盘；密钥文件 0600；API 只返回掩码 |
| CSRF | 会话内 CSRF token + 写操作校验 + Origin 白名单 |
| 会话固定/劫持 | HttpOnly/SameSite Cookie（`chaoxing_panel`，默认 12 小时）；可选 Secure；改密/注销全部时 session_version 失效所有旧会话 |
| 暴力破解 | 登录 IP/用户名滑动窗口 + 失败锁定（默认 5 次/15 分钟） |
| 接口滥用 | 全局 300 请求/分钟/IP；登录 10 次/5 分钟/IP；可用 API Key + 代理层限流 |
| XSS | 前端全部 `textContent` 渲染；CSP 禁止内联脚本；无 `innerHTML` |
| 点击劫持 | CSP `frame-ancestors`（默认允许 `self` 与 Arena 预览域；生产建议改为 `'self'`） |
| 敏感日志 | Cookie/Authorization/token/通知 URL 统一脱敏；账号日志分文件 |
| 审计 | 登录、账号操作、设置修改、启动/停止写入 `data/audit.log`（5MB 自动轮转） |
| 错误处理 | 所有错误走面板自己的页面/JSON，不回显框架默认页与堆栈；500 带错误编号便于对日志 |
| 资源耗尽 | 登录失败记录、限流桶都有上限与过期清理，随机 key 刷接口不会撑爆内存 |
| 数据损坏 | `accounts.json` / `settings.enc` 解析失败时先备份为 `*.corrupt-<时间戳>` 再继续，不会静默覆盖 |
| 输入清洗 | 账号名称/备注等字段过滤控制字符，防止伪造日志行 |
| 容器 | 非 root、`no-new-privileges`、`.dockerignore` 排除本地凭据 |
| 无人值守 | 手动停止的账号不会被自动重启；自动重启有次数上限，防止账号密码错误时被反复锁死 |

### 6.2 未实现 / 需要注意

1. **没有 MFA/SSO**，管理员密码是唯一入口；建议放在 VPN/内网或加代理层认证。
2. **速率限制是进程内内存实现**，多实例/重启后清零；生产多实例请用 Redis/网关限流。
3. **API Key 是全权限、单一密钥**，没有按账号/按接口的 scope。
4. **没有多租户/RBAC**；面板默认假设“一个团队/一个人管理自己的账号”。
5. **Cookie/密码一旦进入内存/日志仍有泄露面**；日志已脱敏，但不要开启 debug 输出完整请求。
6. **超星平台条款/学校纪律风险**：本面板只是执行器，不改变自动化刷课本身的合规风险；请确认你有权使用自己的账号进行这些操作。
7. **平台改版风险**：题库、视频上报、字体加密等依赖页面结构，平台升级后可能失败；面板会显示失败状态而不是假装成功。
8. **单进程**：状态在内存，重启后运行中的任务会中断；需要持久化任务队列时应另行设计。

### 6.3 首次初始化的特别说明

如果面板在公网且尚未初始化，任何人都可能抢先创建管理员。请：
- 先在本地/内网执行 `python panel.py --init-admin`，或
- 设置 `PANEL_SETUP_TOKEN`，初始化时在请求头带 `X-Setup-Token`；
- 初始化完成后确认 `/api/auth/status` 返回 `configured: true`。

---

## 7. 数据、备份与删除

```text
data/
├── admin.json          # 管理员用户名 + scrypt 哈希 + session_version
├── secret.key          # 加密账号密码/Cookie 的 Fernet 密钥（权限 0600）
├── accounts.json       # 账号配置（敏感字段为密文）
├── settings.enc        # 题库/通知设置（整体加密）
├── session.key         # Flask 会话签名密钥（可用 env 覆盖）
├── audit.log           # 审计日志
├── accounts/<id>/cookies.txt
└── logs/<id>.log
```

- **备份**：`data/` 整个目录（尤其 `secret.key` + `accounts.json`）。
- **迁移**：保持 `PANEL_DATA_DIR` 不变；更换 `secret.key` 会导致旧凭据无法解密。
- **删除账号**：面板会先停止运行器，再删除该账号的 cookie/日志目录；`audit.log` 保留操作记录。
- **重置管理员密码**：`python panel.py --reset-admin`（推荐，立即生效且不影响账号数据）；
  或在停止面板后删除 `data/admin.json` 重新初始化；删除整个 `data/` 会丢失账号。

文件权限建议：

```bash
chmod 700 /opt/chaoxing/data
chmod 600 /opt/chaoxing/data/*
```

---

## 8. 常见问题

**Q：启动账号后一直“排队中”？**
A：`max_concurrent_accounts` 已满。停止一个运行中的账号，或把设置调大（注意服务器与超星风控压力）。

**Q：停止一个账号后，另一个账号也停了？**
A：不应发生。每个账号使用独立 `RunControl`；请确认没有点击“停止全部”，并检查日志中是否有 `账号任务已停止` 与另一个账号的继续记录。若复现请提供两个账号的日志与操作时间。

**Q：暂停后日志不动？**
A：暂停会阻塞安全点，正在进行的单次 HTTP 请求会先完成；视频进度循环、重试等待、30 秒学习次数都会立即响应。

**Q：账号显示“失败”，但课程页面看起来完成了？**
A：面板以章节任务结果为准；可能有个别章节未开放/题库覆盖率不足/视频 403。查看该账号日志与错误信息，处理后再启动；不要直接相信“进程还在跑”。

**Q：修改设置后正在运行的账号会生效吗？**
A：不会。运行中的账号在启动时读取配置快照；修改后需停止并重新启动该账号。

**Q：`PANEL_API_KEY` 怎么轮换？**
A：替换环境变量并重启面板；旧 key 立即失效。轮换不会影响浏览器会话（除非同时修改了 session secret）。

**Q：管理员密码忘了 / 用户名忘了？**
在服务器上用同一个 `PANEL_DATA_DIR` 执行：

```bash
cd /opt/chaoxing
PANEL_DATA_DIR=/opt/chaoxing/data .venv/bin/python panel.py --reset-admin
# 免交互（容器/脚本）：
PANEL_DATA_DIR=/opt/chaoxing/data PANEL_ADMIN_USER=admin \
  PANEL_ADMIN_PASSWORD='New-Password-2026' .venv/bin/python panel.py --reset-admin

# Docker
docker compose -f docker-compose.panel.yml exec \
  -e PANEL_ADMIN_USER=admin -e PANEL_ADMIN_PASSWORD='New-Password-2026' \
  panel python panel.py --reset-admin
```

- 输出会打印当前用户名与 `admin.json` 的实际路径，先确认改的是对的数据目录；
- 新密码**立即生效**（`PanelAuth` 会热加载 `admin.json`），旧密码失效、所有旧会话被登出、失败锁定被清除；
- 超星账号、题库、通知、审计日志都不受影响；
- 兜底方案：停面板 → 删 `data/admin.json` → 重启回到初始化页（只丢管理员账号）。

**Q：为什么不能 `gunicorn -w 4`？**
A：每个 worker 进程都有独立的 `RunnerManager` 和内存状态，会把同一账号启动 4 次。必须 `-w 1`；要扩展请先做分布式状态层。

---

## 9. 本次 P0 修复对照

| 问题 | 修复 |
|---|---|
| worker 异常导致主线程永久阻塞 | worker 顶层兜底异常→`ChapterResult.ERROR`；`task_done` 与重试记账成对；停止时排空队列并唤醒 worker；新增回归测试 |
| `SessionManager` 单例失效 | 改为线程本地会话 + 显式绑定；每个账号/worker 独立 `requests.Session`，Cookie 文件隔离 |
| 失败仍提示成功 | `JobProcessor.summary()` + CLI 退出码 + 面板状态；空任务点/直播失败不再静默成功 |
| 指定课程无匹配 → 全量课程 | `filter_courses` 抛出 `InputFormatError`；只有 `*` 才表示全部；非交互环境不会阻塞 `input()` |
| 章节并发破坏顺序 | 默认 `course_serial=true`（课程内串行、课程间并行），`PriorityQueue` 使用 `(course_index, chapter_index)` |
| 凭据日志泄露 | Cookie 日志删除；日志统一脱敏；账号密码/Cookie 加密存储 |
| 缺少超时/停止控制 | 会话默认 15s 超时；`RunControl` 贯穿视频循环、重试、学习次数、直播 |
