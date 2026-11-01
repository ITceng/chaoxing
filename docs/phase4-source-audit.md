# 阶段四源码核查与适配边界

核查基线：`arena/bd5e1acd-chaoxing`，起始 HEAD `af955617c64141f039ae7b8b324d15566c7548a3`。本轮开始时工作区干净。新增阶段四实现仍限定在 `unified_system/`；既有账号授权流程、`fuckZHS-master/` 及 legacy 平台项目均未覆盖或修改。

## 当前统一系统基线

- 阶段二/三源码存在：`unified_system/api/`、`models.py`、Alembic、Vue 前端 `frontend/`。此前迁移为 `0001_initial`、`0002_user_sessions`；没有阶段四课程缓存或授权挑战迁移。
- 平台目录 API 为 `GET /api/v1/platforms`；个人/共享账号 API 在 `api/accounts.py` 和 `api/shared_admin.py`。
- 内部 Worker API 为 `/api/internal/workers/{heartbeat,claim,shutdown,<task>/start,<task>/heartbeat,<task>/complete}`，鉴权头为平台、Worker ID、平台专用 token；claim 响应只在有效租约下返回当前任务凭证。`shutdown` 是本轮新增的兼容性扩展，显式持久化 Worker 离线状态。
- 任务持久状态使用数据库 `tasks`、`task_events`、`account_execution_leases`；状态值在代码中为小写 `queued/leased/running/cancel_requested/succeeded/failed/cancelled/timed_out`。个人归属、shared grant、账号槽位锁和 fencing token 已在后端实现。
- 根目录 `Dockerfile`、`Dockerfile.panel`、`docker-compose.panel.yml` 均属于原学习通程序/旧面板，不是统一系统开发环境；本环境没有 Docker CLI/Compose，不能声称镜像构建或容器联调通过。
- 阶段四开始时基线测试：后端 30 passed，前端 Vitest 5 passed、TypeScript 检查通过。阶段四新增测试结果另见本阶段交付记录。

## 学习通（只读源码核查）

- 当前存在 `platforms/chaoxing.py`；它不是独立安全 Worker，而是延迟调用旧 `main.init_chaoxing` 的调度适配器。它声明了 video/audio/document/work/read/live 能力，其中包含课程状态写入、学习任务与答题相关逻辑，不能整体直接暴露给多用户服务。
- `api/base.py` 的 `Chaoxing` 实现包含登录、`get_course_list`、课程章节读取，同时也包含 `study_video`、`study_work`、打卡/次数等写入路径。线程本地 `SessionManager` 最终在无显式 Cookie path 时回落到旧项目全局 Cookie 文件。未在未隔离/未审计的情况下导入或调用这些路径。
- `unified_system/platforms/chaoxing/adapter.py` 保持 cookies-only、未注册候选。除固定 HTTPS/禁重定向/隔离 Session/合作取消外，本轮增加 streamed 2 MiB 响应上限、CookieJar 清理、请求超时细分和 `check_session_authorization`（只读课程响应检查）；未识别/空目录不会误报授权成功。`manual_verify` 将 credential 状态与 integration 状态分开：真实烟测才会显示凭证 `VERIFIED`，平台集成仍为 `DISABLED`，Mock 默认只显示 `PENDING_VERIFICATION`。验证码/交互返回待用户处理，不自动重试。功能能力仍全部 false、未注册 Worker；只有 Mock 验证，没有真实平台授权或网络测试。

## 智慧树 / 知到

- 旧 `platforms/zhihuishu.py` 为计划骨架；实际可审计源位于 `fuckZHS-master/zhs_auth.py`、`fucker.py`、`zd_utils.py`（不是 `auth.py`）。`zhs_auth.py` 包含用户密码/官方二维码与验证码状态，但不会在统一 Worker 中自动操作；本轮 adapter 只接受账号所有者提供的会话 Cookie。
- 新增 `unified_system/platforms/zhihuishu/adapter.py`，仅封装旧 `getZhidaoList` / `getHikeList` 的两种课程目录读取请求和必要 AES 请求信封；不导入 `Fucker`，因为旧构造器和请求路径会记录 Cookie/header/body，且类包含学习进度写入、考试/答题/提交方法。新代码严格限制固定 HTTPS endpoint、独立 session、响应/分页上限、请求超时、错误码脱敏、CookieJar 清理与协作取消；能力全 false、健康检查不通过、没有注册 Worker。
- 智慧树当前为代码已实现、Mock 已验证；真实登录/授权、业务响应字段和课程列表均未验证。出现验证码/安全验证返回 `interaction_required`，不绕过。

## 英华源码来源结论（以本工作区为准）

- 根目录 `mooc-main/` **不存在**；当前工作树/已跟踪文件没有 `.go`、`go.mod` 或 `go.work`。当前本地及远端可见分支只有 `arena/bd5e1acd-chaoxing` 和 `main`；`origin/main` tree 同样没有 `mooc-main` 或 Go 源码。因此目前没有可审计的英华 Go/Linux Worker 源码；未执行 Windows EXE 或 Tampermonkey 内容。
- `英华等平台/` 当前有 Windows `.exe` 和配置文件，没有 Go 源码。该二进制不等同于可重现的 Go Worker，未尝试运行或提取。
- `Yinghua_auto_answer_script-main/` 是独立的浏览器 Userscript/AI 问题库项目；其 README 将其描述为浏览器端答题自动化，前端包含 `yinghua.js` 和 `yuketang.js`，后端为 Python API。它不是英华服务器端账号适配器，不能当作 Go 源码或后台 Worker。
- 以上三类实现保持明确区分；英华目前不具备可验证的服务端接入条件，状态维持待接入。详见 [`yinghua-integration-blocker.md`](yinghua-integration-blocker.md)。

## 安全/验证边界

- 仓库中存在由旧项目跟踪的配置/Cookie/key 类文件名；本次没有读取、输出、复制其值，也没有导入旧 AI Provider 配置。若这些文件含仍有效的第三方凭证，所有者应在供应商侧自行撤销/轮换；统一系统不替代该操作。
- 未提供实际授权测试账号/测试环境，本次没有向学习通或智慧树发起真实登录，也不以模拟状态冒充真实授权验证。
- 阶段四只允许读取课程/资源/进度；任务队列拒绝非允许清单的写入型任务。题目答案生成、测验/考试、打卡、观看/进度写入和代替用户提交不在适配器合同内。

## Phase 4A implementation checks (2026-11-01)

- Worker API retains platform-wide shared-token compatibility and now optionally binds configured tokens to `<platform>:<worker_id>`; once Worker-specific keys exist, unknown IDs cannot use a platform-token fallback. It remains on the existing persistent `Task`, `TaskEvent`, `AccountExecutionLease` and `WorkerInstance` models; this round adds no in-memory task queue or database migration. Task claim/start/heartbeat/complete and account fencing continue using the phase-two schema. Added authenticated `shutdown` to persist offline state, explicit HTTP response closure, consumed-envelope credential/token clearing, and fail-stop behavior when a lease is lost or adapter ignores cancellation. Numeric task-percent reporting is not implemented; current persisted status/event transitions are the authoritative progress signal.
- Existing platform-key/Worker-ID/shared platform-token authentication and platform matching remain compatible. Task capabilities are still an explicit read-only allowlist and a Worker may claim only the configured platform's tasks. The shared account grant is rechecked at claim and completion; DB account execution lease remains the account-level serialization lock.
- Learning Platform: capability-disabled candidate with authorization query, session validity/error classification, bounded streaming responses, cleaned CookieJar, course ID/name/teacher parse, and a hidden-input local smoke tool. Credential and integration states are separated across `NOT_CONFIGURED`, `PENDING_VERIFICATION`, `VERIFIED`, `EXPIRED`, `ERROR`, `DISABLED`; integration remains `DISABLED`. Mock tests only; no live test.
- Zhihuishu: added a separate cookies-only read candidate for the legacy shared/campus course-list request shapes. It does not import the old automation class and cannot dispatch because all capabilities remain false. Mock tests only; no live authorization or data test.
- Personal/shared authorization workflow and migrations `0003_platform_courses`, `0004_account_verification` remain intact. Course cache and account verification remain database-backed, ownership/grant-isolated and lease-validated.
- Executed on the Debian Linux sandbox: `.venv/bin/pytest -q unified_system/tests` **63 passed**; `.venv/bin/python -m compileall -q unified_system`; `npm run test --prefix unified_system/frontend` **5 passed**; `npm run typecheck --prefix unified_system/frontend`; `npm run build --prefix unified_system/frontend`; `git diff --check`. Fresh temporary SQLite `alembic upgrade head` and `alembic check` reached 0004 with no metadata drift. No PostgreSQL server was available; PostgreSQL runtime/concurrency behavior is not claimed as tested.
- Runtime here is Debian GNU/Linux 12 x86_64 / Python 3.11.2 (sandbox), not the target Ubuntu server. Linux venv paths/imports and Python tests were checked here; target-server/Ubuntu behavior is **not** claimed as tested. Docker/Compose and Go builds are not run. No real platform login, authorization, course retrieval, or platform-specific build has been verified.
