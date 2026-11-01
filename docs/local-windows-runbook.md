# 本地 Windows 运行手册（单机、手动启动）

**目标：**在用户自己的 Windows 10/11 电脑上运行本仓库中的统一系统，只监听 `127.0.0.1`；开机不自启，不安装 Windows 后台服务，不用 Docker/Compose、Nginx 或 Redis。关闭时只停止本项目后端，不停止 PostgreSQL。

**重要状态边界：**Arena 当前工作区是 Debian 12，不是用户的 Windows 电脑；不能读取 `D:\project\chaoxing`，不能检查用户电脑上的 Python/.venv/Node/PostgreSQL/端口，也不能实际双击 Windows `.bat` 或执行 PostgreSQL 验收。因此这里提供可在目标 Windows 上运行的只读预检和本地管理脚本，不能把它们描述成已在用户电脑实测。用户本机结果需由 `检查本机环境.bat`、`查看运行状态.bat` 和逐项验收产生。

本次检查到的 Arena Git 基线是分支 `arena/bd5e1acd-chaoxing`、HEAD `4de1535`，当时工作区干净。目标电脑的分支、HEAD 和未提交更改未知；先在目标仓库运行预检，不要强制切换分支、reset、清理工作区或覆盖原始文件。

需要把本轮启动脚本拿到 Windows 仓库时，先在 `D:\\project\\chaoxing` 检查 `git status --short --branch` 和 `git log -1 --oneline`。只有确认本地分支为 `arena/bd5e1acd-chaoxing` 且工作区干净时，才可快进更新：

```powershell
git fetch origin
git pull --ff-only origin arena/bd5e1acd-chaoxing
```

如果分支不同或有未提交更改，先停止，不要 checkout/reset/force；备份并审查本地更改后由用户决定如何整合。该步骤不覆盖原始项目文件。

## 1. Windows 运行结构

- 后端入口不是根目录 `app.py` 或 `main.py`：统一系统工厂为 `unified_system.app:create_app`，WSGI/应用工厂在 `unified_system/wsgi.py`。
- 本地服务使用仓库 `.venv\Scripts\python.exe`，通过随 Flask/Werkzeug 安装的本地服务器运行自定义 `unified_system.local_server`，绑定 `127.0.0.1:8088`，debug/reloader 均关闭。
- 前端是 `unified_system/frontend` 下的 Vue/Vite。启动器检查 Node/npm 与 `node_modules`；源码变化时才构建 `frontend\dist`。Flask 在同源的 `127.0.0.1:8088` 提供构建后的 Vue 页面和 `/api/v1`。因此最终运行时**没有独立 Vite 进程，也不占用 5173**；Node/npm 仅用于依赖检查和需要时构建。
- 本地业务数据库必须是 Windows PostgreSQL 服务，不接受 SQLite 配置。当前迁移 head 是 `0005_operational_alerts`。启动只检查连接和数据库 revision；不会自动建库、执行 migration、downgrade、清库或重建。
- 现有 worker runtime 没有经验证的三个平台执行适配器。启动器不会启动任何 Worker；状态输出会明确显示它们禁用/未验证。

本地端口：`127.0.0.1:8088`（前端和后端同源）、配置的 PostgreSQL 本机端口（默认 5432）。没有公网监听。启动器发现端口被其他程序占用时会失败并报告，不会结束陌生 PID。

## 2. 一次性检查与安装（不由启动器自动安装）

在项目根目录 `D:\project\chaoxing` 双击 `检查本机环境.bat`，或 PowerShell：

```powershell
Set-Location D:\project\chaoxing
.\scripts\windows\preflight-local.ps1
```

预检不启动服务、不安装软件、不连接数据库、不运行迁移，也不打印数据库 URL/密码。它报告 Git 分支/提交/工作区变更数量、Windows/Python/.venv/Node/npm/Git/PostgreSQL/前端依赖/迁移文件，以及 5432、8088、5173 的监听情况。

推荐版本：Python 3.12、Node.js 22 LTS、PostgreSQL 16、Git for Windows。若 PostgreSQL 服务不存在，预检会报告缺失；请由用户按 PostgreSQL 官方 Windows 安装方式手动安装，不要通过启动脚本安装。安装 PostgreSQL 时保留已有 data directory，不要卸载/初始化覆盖已有实例。

根目录 BAT 会对单次 PowerShell 进程使用 `-ExecutionPolicy Bypass`，不修改系统/用户的持久执行策略。手动运行 `.ps1` 时，若执行策略阻止脚本，可用单次调用，例如 `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\configure-local.ps1`；这不会修改持久执行策略。

首次准备仓库依赖（只执行一次，或只有依赖锁文件变化时执行；必须在仓库根目录）：

```powershell
Set-Location D:\project\chaoxing
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    py -3.12 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -r .\unified_system\requirements.txt
Set-Location .\unified_system\frontend
npm ci
Set-Location ..\..
```

如果已有 `.venv`，先检查它是否为当前仓库的虚拟环境；不要删除/覆盖已有虚拟环境。若依赖导入失败，按上面命令修复一次。日常双击不会运行 pip install、npm ci，也不会重新安装依赖。前端源文件有较新修改时，启动器可能运行 `npm run build`（不重新安装依赖），构建日志在本地日志目录。

## 3. PostgreSQL 一次性准备

1. 检查服务：`Get-Service *postgres*`。预检也会报告服务名称、状态和可识别的版本。
2. 建议 PostgreSQL 16，并创建**专供本应用的本机登录角色**和一个新的空数据库，例如 `unified_local_app` / `unified_local`。使用 pgAdmin 的 Login/Group Roles 和 Databases 界面设置独立密码、数据库 owner；不要把应用作为日常运行的 `postgres` 超级用户，也不要覆盖已有数据库。
3. 如果电脑已有课程/用户数据，先用第 7 节备份并核实可读后再做迁移。
4. 运行 `scripts\windows\configure-local.ps1`，选择 PostgreSQL Windows 服务，填写端口、已存在的数据库名、角色名并安全输入数据库密码。配置文件写入 `unified_system\data\windows-runtime\local.env`，不在 Git 中，ACL 限定当前 Windows 用户和 SYSTEM。该文件含数据库连接密码、应用 session 密钥和凭据加密 keyring，在本机以明文保存（受文件 ACL 保护）；不要复制到仓库、提交或共享。请将其另行保存到受 BitLocker/强 ACL 保护的离线位置；若丢失 keyring，数据库中已加密的账号凭据将无法解密。该配置备份必须与 PostgreSQL dump 分开保管。脚本不会创建、清空或迁移数据库。
5. 确认业务数据库确实存在且 PostgreSQL 角色可连接。若使用 pgAdmin，请连接到 `127.0.0.1`/配置端口，并核实数据库 owner/权限。
6. 确认本项目后端已停止后，初始化/前向迁移只需显式运行一次：

   ```powershell
   .\scripts\windows\initialize-local-db.ps1
   ```

   它先启动所选的既有 PostgreSQL Windows 服务（需要权限时会报错，不会自行提权），展示数据库连接和当前/目标 Alembic revision，要求先输入 `BACKUP_VERIFIED:<数据库名>`（已有数据）或 `NEW_EMPTY:<数据库名>`（明确确认新空库），再输入 `MIGRATE:<数据库名>`，才执行 `alembic upgrade head`、`alembic check` 和幂等 `seed-catalog`（初始角色/权限/平台目录）。只做向前 migration，不包含 downgrade/drop/重建。迁移前先有可验证备份；已存在数据的数据库只在确认目标正确、备份完成后执行。

若 `configure-local.ps1` 没发现 PostgreSQL 服务，它会失败并说明需要人工安装；不会下载软件、创建服务或改动 PostgreSQL 数据目录。`scripts\windows\start-database.ps1` 可以单独检查并启动配置过的 PostgreSQL Windows 服务；已运行时不会重复启动。

## 4. 日常启动与停止

初次配置、依赖准备和 migration 完成后，在资源管理器中双击仓库根目录的：

- `启动项目.bat`：定位仓库根目录；记录当前 Git branch/commit/变更数；检查 Python/.venv、Node/npm、依赖、前端构建、合成凭据 keyring 自检、PostgreSQL 服务/连接和 Alembic revision；仅当所有门槛通过时启动本项目后端，等待 `/api/v1/health/ready` 成功后打开默认浏览器到 `http://127.0.0.1:8088/`。
- `查看运行状态.bat`：显示 PostgreSQL 服务/端口/连接，后端进程及数据库就绪检查，前端同源页面 HTTP 检查，Worker 状态，监听端口、最近启动时间和日志路径。
- `停止项目.bat`：写入仅与本项目 PID 匹配的本地停止请求，Werkzeug server 正常关闭；不会终止其他 Python/Node 进程，不会停止 PostgreSQL，不会删除数据库、配置或日志。

PowerShell 手工入口等同：

```powershell
.\scripts\windows\start-local.ps1
.\scripts\windows\status-local.ps1
.\scripts\windows\stop-local.ps1
```

如果服务正常，默认浏览器访问 `http://127.0.0.1:8088/`；后端健康检查 `http://127.0.0.1:8088/api/v1/health/ready`。可在防火墙策略不变的前提下确认只监听 loopback。没有 Windows 服务常驻、开机启动、DNS/公网端口或 Nginx 配置。电脑重启后再次双击即可启动；PostgreSQL 可能由其 Windows 服务设置自动运行，但**本项目本身不会开机自启**。

脚本和 runtime 日志保存在 `unified_system\data\windows-runtime\logs`（被 Git 忽略）。配置和运行数据位于 `unified_system\data`（同样被忽略）；启动器不会清理它们。

## 5. 初始化管理员

迁移后且第一次运行时，在仓库根目录打开 PowerShell，运行一次：

```powershell
.\scripts\windows\bootstrap-local-admin.ps1
```

脚本从受 ACL 保护的本地配置读取数据库连接，只在当前 PowerShell 进程设置数据库密钥；管理员邮箱/密码通过提示输入（密码不回显、至少 12 位），然后立即清除相关环境变量。不要重复创建已有 superadmin；CLI 会拒绝该操作。不要把密码写入脚本、仓库或聊天。

## 6. 日志、失败处理和故障排查

- `launcher.log`：每次预检、数据库服务操作、成功/失败摘要；不会记录 `.env` 内容。
- `backend.<启动时间>.stdout.log` / `backend.<启动时间>.stderr.log`：后端每次运行的输出和错误，历史文件保留。
- `frontend-build.<启动时间>.log`：必要时的前端构建。
- `database-migration.<启动时间>.log`：显式 migration/check 输出。
- PostgreSQL 连接失败：确认所选服务状态、端口、pg_hba 配置、本机角色/密码、数据库是否存在；`configure-local.ps1` 不会覆盖既有配置。需更换连接时，先备份 `local.env`，只编辑 `DATABASE_URL`、`POSTGRES_SERVICE_NAME`、`LOCAL_DATABASE_NAME`、`LOCAL_DATABASE_PORT`，原样保留 `APP_SECRET_KEY`、`CREDENTIAL_ENCRYPTION_KEYS_JSON` 和 `CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID`。不要运行向导生成新 key 后指向已有数据库；key 不匹配会使加密凭据无法解密。切换数据库前核实其内容和备份，不要把密码贴入 issue/chat。
- `8088` 占用：脚本会报告监听 PID，不会杀进程。先用 Windows 资源监视器/任务管理器确认拥有者并自行处理；不要批量结束 `python.exe`。
- Alembic 状态不匹配：启动会停止；备份后显式运行 `initialize-local-db.ps1`。不会在每次启动自动迁移。
- 前端构建失败：看 `frontend-build.log`。只有用户手动执行 `npm ci` 才会下载/重装前端包。
- 后端启动失败/健康检查超时：看 stderr 和 launcher log。启动失败时不会打开浏览器；停止脚本只处理已验证为本项目的 PID。
- 服务停止异常：停止脚本会保留 PID/停止请求并报告，不会无条件 `taskkill /F`。检查日志和对应 PID 后再做人工处理。

## 7. 本地备份与恢复

备份数据库：

```powershell
.\scripts\windows\backup-local-db.ps1
```

该脚本用所选 PostgreSQL 安装目录的 `pg_dump.exe` 生成 custom-format 全库逻辑备份，并用匹配的 `pg_restore --list` 验证目录表、计算 SHA-256。文件保存在 `unified_system\data\backups`，ACL 限定当前用户和 SYSTEM。**此本地 dump 未加密**；使用 BitLocker 保护磁盘并保护 Windows 账户，不要提交 Git、放入网站目录或发送到聊天。保留至少一份离线/独立介质备份并验证 checksum。

恢复时不要对正在使用的数据库运行 `pg_restore --clean`，也不要把恢复导入当前数据库覆盖数据。推荐：

1. `Get-FileHash -Algorithm SHA256 <dump>` 与同名 `.sha256` 清单核对。
2. 在 pgAdmin 创建一个全新的空恢复数据库（例如 `unified_restore_YYYYMMDD`），owner 使用应用角色；绝不选生产/当前使用的 `unified_local`。
3. 使用 pgAdmin 的 Restore，格式选 Custom，选择备份文件，不启用清理/删除对象选项。
4. 对恢复库检查 Alembic revision、必要表/关系和可读性；验证后保留它或手动删除**这个明确创建的恢复库**。不要脚本自动删除恢复数据。
5. 如需把恢复数据作为当前数据使用，先停止项目、再制作当前库的独立备份、复核数据库名和恢复计划；该替换步骤不由一键启动/停止脚本执行。

不要提交本地 dump、SHA 清单或 `windows-runtime` 配置。

## 8. 平台功能边界

本地运行不等于平台集成。现有平台目录中，Chaoxing 和 Zhihuishu 是仍禁用的只读候选，能力均未启用且没有真实授权验证；Yinghua 没有可验证、可构建的当前平台适配器。Worker 状态默认关闭，启动脚本绝不启动它们。

本轮 Windows 本地改造只负责安全运行系统、检查 PostgreSQL/migration、管理后端和构建前端，不新增平台自动化。不会伪造学习进度、自动代学、完成考试/考核、提交答案或绕过平台安全验证。用户提出“三个平台都可正常刷课”与此安全范围及现有能力不相容：当前不能交付或宣称这三项功能可用。只有经平台/学校明确授权且限定为允许的读取/辅助行为，后续才可分别审查适配器；未验证的平台继续显示待接入。

## 9. 验收清单和本次证据边界

用户在 Windows 目标电脑上完成配置后，请记录：

1. `检查本机环境.bat` 输出（检查后去除用户名/机器名等非必要信息）。
2. `启动项目.bat` 正常启动，PostgreSQL 真实连接、迁移头 `0005_operational_alerts`、ready endpoint 成功，前端页面 HTTP 200。
3. 手工验证登录/退出、邀请码创建/使用（不得在报告中贴口令/邀请码）。
4. 对本人授权的个人平台账户管理、共享账户授权/撤销做无真实平台写入的应用层检查；平台账户/课程读取继续标为未验证，不能把 mock 当实时验证。
5. 创建测试记录，停止/重启后验证 PostgreSQL 数据持久化。
6. `停止项目.bat` 只退出本项目进程，PostgreSQL 服务仍运行；再次启动恢复同一数据库/前端。
7. 重启 Windows 后不自动启动本项目；手工双击再次成功。
8. 启动失败时验证日志有原因、不会打开假成功页面、不会终止其他服务。

### 已有检查结果（Arena，不是 Windows 验收）

- 本轮开始时 Arena 分支 `arena/bd5e1acd-chaoxing` 位于 `4de1535` 且工作区干净；目标电脑本地分支仍未知。
- Arena 是 Debian 12 / Python 3.11.2 / Node 22.22.3 / npm 10.9.8。Git 可用；Windows PowerShell/CMD 和 PostgreSQL/`psql` 不可用。
- Portable backend tests：**152 passed, 2 skipped**（跳过项需要 PostgreSQL）；frontend Vitest **5 passed**，typecheck/build 通过；Python 新增模块语法编译通过。
- 新的 Windows PowerShell/BAT 脚本没有在 Windows PowerShell 解释器中解析/运行；PostgreSQL service/连接、Alembic migrations、backup/restore、管理员登录、前端 HTTP、权限、端口占用和正常 stop 流程都没有在目标机测试。上述 portable 测试不是 Windows/PostgreSQL 验收。

当前 Arena 无 Windows PowerShell/CMD、无 PostgreSQL/psql，也无法访问 `D:\project\chaoxing`，故 Windows `.bat`、服务权限、实际 PG 连接、迁移、账号登录、持久化、停启和重启验证均**未执行**。在用户本机完成验收前，不能宣布“Windows 本地部署完成”。停止范围限定为本轮 Windows 本机启动管理；不进行服务器部署。
