# 接入新平台（智慧树 / 学习通以外的平台）

> 结论先给：**可以接入，架构已经做好了**，剩下的每个平台都需要一份"适配器"——
> 也就是把该平台的登录、课程、章节、任务点翻译成统一接口的那几百行代码。
> 这个仓库已经内置：超星（完整）、演示平台（不联网，用来验证链路）、智慧树（骨架，待实现）。

---

## 1. 现在是多平台了吗？

是。调度层（重试、并发、暂停/停止、日志、无人值守、通知）**完全不认识"超星"**，
它只要求平台实现 9 个方法 + 3 个属性：

```python
login(login_with_cookies=False) -> {"status": bool, "msg": str}
get_course_list()               -> [{"courseId", "clazzId", "cpi", "title"}, ...]
get_course_point(courseId, clazzId, cpi) -> {"points": [{"title", "has_finished", "knowledgeid"}]}
get_job_list(course, point)     -> (jobs, job_info)
study_video(course, job, job_info, _speed, _type) -> StudyResult
study_document(course, job)     -> StudyResult
study_work(course, job, job_info) -> StudyResult
study_read(course, job, job_info) -> StudyResult
get_uid()                       -> str

control        # RunControl：暂停/停止检查点
rate_limiter   # 有 limit_rate()，用来限速
new_session()  # 每个 worker 线程独立的 requests 会话
```

`job_info` 里至少要能提供 `notOpen`（章节未开放）与 `knowledgeid`；`job["type"]` 取值
`video` / `document` / `workid` / `read` / `live`。

只要满足这份契约，**面板、多账号、独立暂停/停止、无人值守全部立刻可用**，不需要改调度器。

已有的平台：

| key | 名称 | 状态 | 说明 |
|---|---|---|---|
| `chaoxing` | 超星学习通 / 泛雅 | ✅ 稳定 | 视频、音频、文档、章节测验、阅读、直播、题库、学习次数 |
| `demo` | 演示平台 | 🧪 演示 | 假数据，不联网，用于试跑与写适配器时对照 |
| `zhihuishu` | 智慧树 / 知到 | 🚧 骨架 | 接口齐全但未实现，面板里不可选 |

代码结构：

```
platforms/
├── base.py         # 契约、PlatformSpec 元信息、错误类型、validate_adapter()
├── registry.py     # 注册表：register_platform / build_adapter / list_platforms
├── chaoxing.py     # 超星：薄封装，转发到 main.init_chaoxing
├── demo.py         # 演示平台（也是最好的模板）
└── zhihuishu.py    # 智慧树骨架：每个 NotImplementedError 就是一张待填清单
```

---

## 2. 最快的验证方式：先用 demo 平台跑一遍

```bash
# 命令行
python main.py --platform demo -u demo -p demo -l '*'

# 面板里：添加账号 → 平台选「演示平台（不联网）」→ 账号密码随便填 → 启动
```

你会看到 2 门课、6 个章节任务点全部成功。这说明多平台链路是通的，
新适配器只要把 demo 的假数据换成真实请求即可。

---

## 3. 新增一个平台：4 个步骤

### 步骤 1：复制模板

```bash
cp platforms/demo.py platforms/yourplatform.py
```

### 步骤 2：实现方法（顺序很重要）

1. **`login()`**
   先做密码登录，能拿到 Cookie 就算成功；返回 `{"status": True, "msg": ""}`。
   失败时返回 `{"status": False, "msg": "人话原因"}`——面板会原样显示给用户。
2. **`get_course_list()`**
   拉"我的课程"，映射成统一字段。注意：
   - `courseId` 要能稳定复现（用于课程筛选）；
   - `clazzId` 用来区分同一门课的不同班级；
   - `cpi` 没有就填 `""`。
3. **`get_course_point()`**
   拉章节树，已完成章节 `has_finished=True`（调度器会自动跳过，省时间也少风控）。
4. **`get_job_list()`**
   把章节里的任务点分类：视频 → `type="video"`，文档/PPT → `"document"`，
   测验/弹题 → `"workid"`，阅读 → `"read"`。
5. **`study_video()` 等**
   真正干活的地方。视频通常要**周期性上报进度**（每 15~30 秒一次），
   务必用 `self.control.sleep(...)` 而不是 `time.sleep(...)`，
   否则暂停/停止会失效、面板会卡住。
6. **返回 `StudyResult`**
   `SUCCESS` / `ERROR` / `FORBIDDEN` / `TIMEOUT`，来自 `api.base`。
   **失败必须返回 ERROR**，不要为了"看起来跑完了"返回 SUCCESS（这会导致面板显示成功但实际没完成）。

### 步骤 3：注册

文件末尾：

```python
SPEC = PlatformSpec(
    key="yourplatform",              # 会写进账号配置，之后不要改
    name="某某平台",
    summary="一句话说明",
    status=STATUS_EXPERIMENTAL,      # stable / experimental / demo / planned
    capabilities=("video", "document"),
    credentials=("username_password", "cookies"),
    docs="限制与注意事项，会显示在面板提示里",
    builder=_build,                  # def _build(config, tiku_config) -> adapter
)
register_platform(SPEC)
```

再把模块加进 `platforms/registry.py` 的 `_BUILTIN_IMPORTS`，面板下拉框里就会出现。

### 步骤 4：自检

```bash
python -c "from platforms import list_platforms; print(list_platforms())"
python -m unittest tests.test_platforms -v      # 契约测试
python main.py --platform yourplatform -u 账号 -p 密码 -l '*'   # 实跑
```

`validate_adapter()` 会告诉你还缺哪个方法。

---

## 4. 需要你提供什么？

写一个平台适配器，**不需要你写代码**，但需要下面这些"素材"——缺一个都会导致实现只能靠猜：

| 需要 | 具体是什么 | 用途 |
|---|---|---|
| **① 抓包文件（最重要）** | 浏览器 F12 → Network → 勾选 Preserve log → 依次打开「登录 / 我的课程 / 某一章的目录 / 视频播放页 / 做完一节」→ 右键 *Save all as HAR with content* | 确定每个接口的 URL、参数、签名、返回结构 |
| **② 一个测试账号** | 最好是小号，或至少提供一次成功登录后的 Cookie | 验证实现，避免反复触发风控 |
| **③ 任务范围** | 只要看视频？还是要做章节测验/见面课/讨论？ | 决定工作量与是否要接题库 |
| **④ 限制条件** | 是否需要倍速限制、防挂机检测、验证码类型 | 决定失败率与重试策略 |

> ⚠️ 抓包里含有你的 Cookie 和密码，**请先脱敏**（把 `Cookie:`、`token`、`password`
> 字段替换成 `***`）再发出来；HAR 文件里的 `Set-Cookie` 也记得删。

拿到 ① 之后，通常的节奏是：

1. 先只做「登录 + 课程列表 + 章节列表」，能列出课程就成功一半；
2. 再单独做「视频进度上报」，用一节短视频验证进度条真的在涨；
3. 最后接「章节测验」（需要题库）与异常处理（未开放、403、超时）。

---

## 5. 平台之间的差异（决定难度）

| 平台 | 登录 | 任务点上报 | 难度 | 备注 |
|---|---|---|---|---|
| 超星 / 泛雅 | 表单 + 可选验证码 | `multimedia/log` 周期上报，参数有加密 | 中（已实现） | 字体解密、题库已适配 |
| 智慧树 / 知到 | 扫码或密码 + `uuid` | `videoStutyWT` 一类接口，校验 `watchPoint` | 中高 | 需要真实抓包；有风控 |
| 中国大学MOOC | 密码 + 图形验证码 | `mooc1` 播放上报 | 中 | 反爬较强 |
| 学堂在线 | OAuth | 心跳上报 | 中高 | 需处理 xuetangX 的加密参数 |

**不建议**接入需要绕过付费/考试监考的平台；也不建议在没有测试账号的情况下"凭经验写"，
那通常结果是"跑完显示成功但后台没进度"。

---

## 6. 常见问题

**Q：接一个新平台要改面板代码吗？**
不用。面板、调度器、无人值守都不感知平台；只有下拉框的选项来自注册表。

**Q：可以一个账号用超星、另一个用智慧树吗？**
可以。平台是**账号级字段**，同一个面板里可以混着跑，暂停/停止依然互不影响。

**Q：智慧树什么时候能用？**
目前只有骨架。等抓到 `passport.zhihuishu.com` 登录、课程列表、`studyvideoh5` 上报这三组接口，
就可以按 `platforms/zhihuishu.py` 里的 6 步清单依次实现。

**Q：可以接入学校自建平台吗？**
可以，只要能抓包看到接口；如果平台是标准 LTI/SCORM，可能更简单。

**Q：第三方开发者能自己写适配器而不改这个仓库吗？**
可以。把你的模块放进 `platforms/` 并在 `_BUILTIN_IMPORTS` 加一行（或用 `register_platform()`
在你的启动脚本里注册），面板就会识别。
