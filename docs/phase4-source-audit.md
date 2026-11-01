# 阶段四源码核查与适配边界

核查基线：`arena/bd5e1acd-chaoxing`，起始 HEAD `0b7a9b7`（Windows 本地运行修复提交）。本次检查开始时工作区干净。原始平台目录保持只读；新 Worker/适配器框架只新增在 `unified_system/`。

## 当前统一系统基线

- 阶段二/三源码存在：`unified_system/api/`、`models.py`、Alembic、Vue 前端 `frontend/`。此前迁移为 `0001_initial`、`0002_user_sessions`；没有阶段四课程缓存或授权挑战迁移。
- 平台目录 API 为 `GET /api/v1/platforms`；个人/共享账号 API 在 `api/accounts.py` 和 `api/shared_admin.py`。
- 内部 Worker API 为 `/api/internal/workers/{heartbeat,claim,<task>/start,<task>/heartbeat,<task>/complete}`，鉴权头为平台、Worker ID、平台专用 token；claim 响应只在有效租约下返回当前任务凭证。
- 任务持久状态使用数据库 `tasks`、`task_events`、`account_execution_leases`；状态值在代码中为小写 `queued/leased/running/cancel_requested/succeeded/failed/cancelled/timed_out`。个人归属、shared grant、账号槽位锁和 fencing token 已在后端实现。
- 根目录 `Dockerfile`、`Dockerfile.panel`、`docker-compose.panel.yml` 均属于原学习通程序/旧面板，不是统一系统开发环境；本环境没有 Docker CLI/Compose，不能声称镜像构建或容器联调通过。
- 阶段四开始时基线测试：后端 30 passed，前端 Vitest 5 passed、TypeScript 检查通过。阶段四新增测试结果另见本阶段交付记录。

## 学习通（只读源码核查）

- 当前存在 `platforms/chaoxing.py`；它不是独立安全 Worker，而是延迟调用旧 `main.init_chaoxing` 的调度适配器。它声明了 video/audio/document/work/read/live 能力，其中包含课程状态写入、学习任务与答题相关逻辑，不能整体直接暴露给多用户服务。
- `api/base.py` 的 `Chaoxing` 实现包含登录、`get_course_list`、课程章节读取，同时也包含 `study_video`、`study_work`、打卡/次数等写入路径。线程本地 `SessionManager` 最终在无显式 Cookie path 时回落到旧项目全局 Cookie 文件。未在未隔离/未审计的情况下导入或调用这些路径。
- 新增 `unified_system/platforms/chaoxing/adapter.py` 候选只读适配器：参考旧 endpoint/字段，但不导入旧 parser、SessionManager、Cookie 文件回退、调度器或密码登录；每次使用独立 Session、固定 HTTPS host、禁用 redirect、超时与合作式取消，只输出课程 ID/名称/教师。它的能力仍全部关闭、健康检查返回待验证，未注册 Worker。当前只通过离线 Mock/parser 测试；没有真实平台授权环境，因此不得宣传可用。若登录/验证码/交互状态出现，返回待用户处理，不重试绕过。

## 智慧树 / 知到

- 根目录 `platforms/zhihuishu.py` 自身明确标记 `STATUS_PLANNED`，构造和主要接口抛出未实现异常；不能称为现成的智慧树适配器。
- `fuckZHS-master/zhs_auth.py` 具有密码登录（验证码 token 必需）及其 QR 登录代码；源码说明存在验证码/安全验证状态。阶段四 Worker 不会绕过验证码或安全验证。实际平台授权仍须用户交互和专用授权状态端点。
- `fuckZHS-master/fucker.py` 同时包含课程查询方法和大量学习进度写入、题目/考试/提交实现；其 Cookie setter 有记录完整 Cookie 的调试日志路径。没有将此模块导入统一系统，避免跨账号状态/日志泄漏和意外调用写入功能。只读查询需先做无副作用、去敏的独立封装并用 Mock 覆盖。

## 英华源码来源结论（以本工作区为准）

- 根目录 `mooc-main/` **不存在**；全仓库没有 `.go`、`go.mod` 或 `go.work`。因此本工作区没有可以验证 Linux 构建的英华 Go 源码；环境也未安装 Go。
- `英华等平台/` 当前有 Windows `.exe` 和配置文件，没有 Go 源码。该二进制不等同于可重现的 Go Worker，未尝试运行或提取。
- `Yinghua_auto_answer_script-main/` 是独立的浏览器 Userscript/AI 问题库项目；其 README 将其描述为浏览器端答题自动化，前端包含 `yinghua.js` 和 `yuketang.js`，后端为 Python API。它不是英华服务器端账号适配器，不能当作 Go 源码或后台 Worker。
- 以上三类实现保持明确区分；英华目前不具备可验证的服务端接入条件，状态维持待接入。

## 安全/验证边界

- 仓库中存在由旧项目跟踪的配置/Cookie/key 类文件名；本次没有读取、输出、复制其值，也没有导入旧 AI Provider 配置。若这些文件含仍有效的第三方凭证，所有者应在供应商侧自行撤销/轮换；统一系统不替代该操作。
- 未提供实际授权测试账号/测试环境，本次没有向学习通或智慧树发起真实登录，也不以模拟状态冒充真实授权验证。
- 阶段四只允许读取课程/资源/进度；任务队列拒绝非允许清单的写入型任务。题目答案生成、测验/考试、打卡、观看/进度写入和代替用户提交不在适配器合同内。

## Phase 4A implementation checks (2026-11-01)

- Added a single read-only task-kind allowlist shared by task creation and Worker heartbeats. An adapter may claim only task kinds in both its heartbeat declaration and the server platform catalog; platform-declared capabilities default to empty. The claim query filters candidates by those kinds.
- The Worker runtime checks adapter health before sending its heartbeat/claiming; unhealthy adapters therefore stop refreshing online status and cannot take more work. A generic Worker Dockerfile is supplied, but it intentionally cannot start without a real adapter factory.
- Added a Learning Platform cookies-only course-list candidate that remains unregistered/disabled. Its parser, redirect/timeout boundary, per-call cookies and cancellation behavior are covered by offline mocks only; this is not a real integration test.
- Added `PlatformCourse` and migration `0003_platform_courses`. Lease-validated `course_sync`/`course_list` completions may include an additive, bounded allowlist of course fields; the Phase 4 Worker sends it, while older protocol clients can omit it. The server persists a minimal per-account catalog and returns only a count in task summaries. `GET /api/v1/courses` enforces personal ownership and live shared-grant scope; revocation immediately hides cached shared courses. Vue route `/app/courses` displays persisted records and explicitly labels the empty state as unsynced.
- Added migration `0004_account_verification` and a personal-account `account_validate` task endpoint. Verification tasks are allowed to claim only unverified/error accounts when the verified platform catalog exposes that capability; the server changes account status only for the minimal positive result. Reauthorization cancels existing account tasks and clears the verification timestamp. Personal and creator-only shared-account flows are backend-simulated in tests only; no platform credential validation has run.
- Fresh SQLite Alembic upgrade and `alembic check` reached `0004_account_verification` with no metadata drift; `pytest -q unified_system/tests`: **46 passed**; `compileall` passed; frontend Vitest **5 passed**, TypeScript typecheck and production `vite build` passed; `git diff --check` passed.
- Docker/Compose and Go CLIs are not installed in this environment. Compose was not built/run; platform network authorization, credential validation, actual platform course retrieval and platform-specific builds remain unverified/not implemented.
