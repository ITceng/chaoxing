# 阶段三前后端 API 合同核查

检查日期：2026-10-31。**仓库当前没有 OpenAPI/Swagger 文件，也没有 OpenAPI 生成器**。因此本报告以 Flask `app.url_map` 实际注册路由、Blueprint 装饰器权限，以及各路由的 JSON 请求/响应实现为依据；没有把阶段二设计清单当作接口事实。路径中 `<id>` 对应 Flask 路由参数。

## 前端直接使用的真实路由

| 方法/路径 | 真实用途与前端调用 |
|---|---|
| `POST /api/v1/auth/login` | `{email,password}`；成功返回 `{user,csrf}` 并设置 HttpOnly session cookie。 |
| `POST /api/v1/auth/register` | `{email,display_name,password,invitation_code}`；服务端检查邀请码/次数并直接建立登录会话。错误码 `400/409` 由页面展示。 |
| `GET /api/v1/auth/me` | `{user,roles,permissions,csrf}`。 |
| `POST /api/v1/auth/logout` | CSRF；撤销当前服务端 session 并清除 Cookie。 |
| `PATCH /api/v1/auth/profile` | `{display_name}`；本人资料。 |
| `POST /api/v1/auth/password` | `{current_password,new_password}`；提升 auth version、撤销会话并要求重新登录。 |
| `GET /api/v1/auth/sessions` | 返回当前用户有效会话 `{id,created_at,last_seen_at,expires_at,user_agent,current}`。不返回 Cookie/session token。 |
| `POST /api/v1/auth/sessions/{id}/revoke` | CSRF；撤销本人指定 session。若为当前 session，服务端清 Cookie。 |
| `POST /api/v1/auth/sessions/revoke-others` | CSRF；保留当前 session，撤销其余本人 session。 |
| `GET /api/v1/platforms` | 实际平台目录字段：`key/name/code_available/integration_status/integrated/auth_methods/capabilities/last_verified_at/status_note`。 |
| `GET /api/v1/platform-accounts` | 返回 `{personal_accounts,shared_accounts}`。共用账号对象已按当前用户授权过滤；不含 secrets。 |
| `POST /api/v1/personal-accounts` | `{platform_key,label,account_hint,auth_type,credentials}`；凭证加密存储；返回掩码账号和未验证状态。 |
| `PATCH /api/v1/personal-accounts/{id}` | 新增兼容接口：只更新本人账号 `label`，服务端校验所有权。 |
| `POST /api/v1/personal-accounts/{id}/reauthorize` | `{auth_type,credentials}`；新版本密文、状态重回未验证。 |
| `DELETE /api/v1/personal-accounts/{id}` | 本人解绑；要求 CSRF。 |
| `GET /api/v1/shared-accounts` | 返回仅开放、当前授权有效的共享账号掩码、范围和到期信息。 |
| `GET /api/v1/activity?limit=N` | 新增本人近期审计事件与任务状态事件；不返回其他用户事件。 |
| `GET /api/v1/admin/dashboard` | 新增后端汇总：可见用户/账号/任务、邀请码权限内统计、真实 Worker 心跳计数、任务失败/超时记录和近期审计。普通管理员统计按 scope/本人管理对象收窄；无权限项为 `null`，不是 0。 |
| `GET /api/v1/admin/users?page&page_size&q&status&role` | 新增 SQL 搜索/过滤/分页；返回 `{users,pagination}`；普通管理员仍只查 scope 用户。 |
| `PATCH /api/v1/admin/users/{id}/status` | `{status:active|disabled}`；服务端范围校验、会话/授权/任务撤销逻辑。 |
| `POST /api/v1/admin/users/{id}/sessions/revoke` | 新增：不禁用账户，单独撤销用户全部会话；范围内管理员可执行。返回撤销数量。 |
| `POST /api/v1/admin/users/{id}/roles` | `{roles:[user,admin]}`；只允许超管；角色调整撤销旧会话。 |
| `POST /api/v1/admin/users/{id}/reset-link` | 服务端一次性重置 token 仅返回一次；面板不尝试从摘要恢复。 |
| `GET/POST /api/v1/admin/invitations` | 列表只返回后缀提示/次数/到期/创建者；创建 `{count,max_uses,ttl_days,note}` 后原码仅在响应一次展示。 |
| `POST /api/v1/admin/invitations/{id}/disable` | CSRF；禁用邀请码。 |
| `GET /api/v1/admin/invitations/{id}/uses` | 返回实际消费用户和时间。 |
| `GET/POST /api/v1/admin/shared-accounts` | 管理者创建/查看其共享账号；创建接受凭证但列表只返回掩码。普通管理员只能管理自己创建的账号。 |
| `PATCH /api/v1/admin/shared-accounts/{id}` | 可更新 `label/is_open/status/available_from/expires_at/max_concurrency`；不能手工设置 active。 |
| `POST /api/v1/admin/shared-accounts/{id}/reauthorize` | 新凭证加密写入，回到未验证。 |
| `POST /api/v1/admin/shared-accounts/{id}/grants` | `{user_id,valid_from,expires_at,scope:{task_kinds}}`；后端检查普通用户身份与管理员 scope。 |
| `POST /api/v1/admin/shared-accounts/{id}/grants/{grant_id}/revoke` | CSRF；撤权并请求取消运行任务。 |
| `GET /api/v1/admin/shared-accounts/{id}/usage` | 管理者/超管的脱敏共享账号使用记录。 |
| `GET /api/v1/admin/audit-logs?page&page_size&q&action&entity_type&from&to` | 新增服务端筛选/分页；仅返回授权范围事件；含脱敏元数据与 actor/target 账号标识。 |
| `GET /api/v1/admin/login-logs` | 实际成功/失败/限流登录结果；路由按 scope 限定，当前固定返回最多 200 行。 |
| `GET /api/v1/admin/platform-workers` | 真实 Worker 心跳和离线状态；没有记录时返回空集合。 |
| `GET /api/v1/admin/scopes`, `POST /api/v1/admin/scopes`, `DELETE /api/v1/admin/scopes/{admin_id}/{user_id}` | 超管管理普通管理员用户 scope。 |

所有前端写请求都携带 `X-CSRF-Token`；Axios `withCredentials=true`，浏览器请求仅使用相对 `/api/v1`。前端角色路由只是导航体验，后端每个接口仍独立执行会话、CSRF、权限和对象范围校验。

## 查到的接口缺口与最小修复

1. **账号备注不可修改**：原有个人/共享创建列表接口存在，但个人账号缺 PATCH，shared `PATCH` 也不接收 `label`。新增个人 PATCH，并令 shared PATCH 接受 label；均保持原创建/查询合同不变。
2. **会话管理缺失**：原有会话只有签名 Cookie 和全局 auth version，不能列出或撤销单一会话。新增 `user_sessions` 和 revision `0002_user_sessions`；登录建立 server-side hash 记录，服务端可撤销单个或其他会话。保留旧阶段二 Cookie 在有效期内使用的兼容逻辑；打开会话页时登记 legacy 会话。
3. **管理首页缺少真实总览**：以新增 `/admin/dashboard` 在权限 scope 内聚合数据库中的实际用户、账号、任务和 Worker 记录；不由前端计算全局数，也不把无权读取的指标伪装成 0。
4. **用户/审计缺少服务端查找**：为用户列表加入 query/status/role 和 SQL 分页；为 audit logs 加 q/action/entity/date 过滤与分页，原响应列表键保留并附加 `pagination`。
5. **用户近期活动缺失**：新增 scope 为本人、由已存在 AuditLog/TaskEvent 构成的 `/activity`，不引入虚构数据。
6. **邀请码创建人未在列表返回**：列表新增 creator ID/email，仅供具备邀请码管理权限的管理员查看。

## 仍然不存在、前端明确空态/限界展示的接口能力

- **系统公告**：没有公告表或 API。Dashboard 显示“暂无公告/功能未接入”，不显示固定模拟公告。
- **平台账号级验证时间**：模型只有平台目录 `last_verified_at`，没有单账号验证事件或验证适配器。账号表标注为未验证；单账号验证时间显示为 `—` 或明确注明字段是平台层记录，不冒充账号验证时间。
- **个人账号“最近使用”时间**：没有独立的账号使用时间戳。Dashboard 将基于用户 `GET /tasks` 的真实任务记录显示“最近任务关联账号”；无任务时显示空态，不以账号创建时间伪称使用时间。
- **真实平台登录/执行**：没有平台 Worker adapter 或可用任务。三平台状态保持 pending，账户状态为 unverified；账号凭证管理不等于能登录或运行课程。
- **Worker 进程异常日志和每 Worker 当前任务**：接口只有心跳/能力/版本，任务统计只支持按平台聚合；面板显示真实 failed/timed_out task 记录，并注明不是 Worker 进程异常报告。
- **审计操作失败结果**：AuditLog 记录成功提交的业务事件，没有所有管理请求失败记录。登录页面另用 LoginLog 显示成功/失败/限流。审计页“已记录”仅代表存在已落库审计事件，不解释为完整请求历史。

## 核验方式

实际 route map 通过 `create_app().url_map.iter_rules()` 枚举；后端每个新增合同都有 Flask 测试。用真实 Flask 后端 Smoke test 需要验证 Cookie 登录/CSRF、未登录 401、权限不足 403、scope 隔离、session 撤销、账号和邀请码操作；任何平台运行能力不在 Smoke test 范围。
