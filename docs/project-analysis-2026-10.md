# 超星学习通自动化工具（chaoxing）项目评估报告

- **评估对象**：`ITceng/chaoxing`（上游 `Samueli924/chaoxing` 的 fork，代码停在上游 `45d2106`，2026-09-28）
- **评估日期**：2026-10-26
- **评估方式**：静态代码审阅（全部 22 个 Python 文件、6317 行）+ 离线复现（17 项单元测试、3 项定向缺陷复现、Ruff/Radon/Vulture 扫描）+ 上游 issue 交叉核对
- **限制说明**：没有使用真实超星账号做联网验证；所有涉及平台服务端行为的结论均标注为"需真实页面/接口确认"

---

## 1. 结论摘要（TL;DR）

**这是一个功能覆盖很广、真实场景打磨较深，但工程可靠性不足的"个人自动化脚本"型项目。**

| 维度 | 判断 | 说明 |
|---|---|---|
| 产品定位 | 单账号 CLI 刷课工具 | 视频/音频、文档、阅读、直播、章节测验、章节学习次数、外部通知 |
| 功能完整度 | 高 | 题库 7 种 provider、可回退、AI/手动/缓存、字体解密、验证码 |
| 架构 | 中等偏下 | 模块有拆分，但 `base.py`/`answer.py` 两个 God Class 占约 57% 的代码 |
| 可靠性 | **低** | 已复现"worker 异常 → 主线程永久阻塞"；失败仍报成功 |
| 安全性 | **低** | Cookie 写日志、`verify=False`、Docker 打包可能带入明文凭据 |
| 可测试性 | 低 | 17 个测试只覆盖调度器和成绩解析，核心网络链路无测试 |
| 工程化 | 低 | CI 只构建 Windows exe，不跑测试/不跑 lint；依赖无锁 |
| 合规风险 | **高** | 模拟学习时长、绕过验证码/反作弊打点、自动答题，违反平台条款与学术诚信的可能性很高 |

**最需要先处理的 5 个问题（均已确认）：**

1. **调度器可能永久卡死**：章节处理抛任何未捕获异常，worker 线程死亡且不调用 `task_done()`，`task_queue.join()` 永不返回（`main.py:376/382-390`，已实测复现）。
2. **`SessionManager` 单例实际失效**：每次 `get_session()` 都会重新构造 `requests.Session`，连接池不复用，运行期更新的 Cookie（验证码、重登）会丢（`api/base.py:39-58`，已实测复现）。
3. **失败被当成成功**：空任务点、直播失败、部分重试耗尽都不会向上传播，最后仍提示"所有课程学习任务已完成"并推送成功通知（`main.py:466-468/322/606-607`）。
4. **课程过滤会退化为"全量课程"**：用户显式指定课程但没有匹配项时，直接学习全部课程；使用配置模板里的 `xxx,xxx` 就会触发（`main.py:531-533`）。
5. **章节并发破坏顺序**：全局优先队列只按"课程内下标"排序，`jobs=4` 会同时启动同一课程的第 0/1/2/3 章，章节解锁/章节检测依赖被打破（`main.py:365-376`；上游 issue #641 已报告同类现象）。

> 这 5 项中，1/2/5 与上游 issue [#618](https://github.com/Samueli924/chaoxing/issues/618) 的描述完全一致。也就是说：**当前 commit 存在已被社区识别、但尚未合入修复的结构性缺陷。**

---

## 2. 项目画像

### 2.1 基本信息

| 项 | 值 |
|---|---|
| 许可证 | GPL-3.0 |
| 版本 | 3.1.3（`pyproject.toml`） |
| 上游热度 | 约 3.7k stars / 476 forks / 36 open issues（2026-10-26 查询） |
| 主要语言 | Python |
| 运行要求 | 源码模式要求 Python 3.13+；提供 PyInstaller Windows exe 与 Docker 镜像 |
| 入口 | `python main.py [-c config.ini] [-u 手机号 -p 密码 -l 课程ID]` |
| 数据持久化 | `cookies.txt`、`cache.json`、`chaoxing.log`（均为本地明文文件） |
| 外部依赖 | 超星 Web 接口、第三方题库/大模型、通知服务 |

### 2.2 项目本质

它不是超星官方 API 客户端，而是**对 Web 端行为的逆向自动化**：

- 用固定 AES 密钥加密账号密码登录，保存 Cookie；
- 抓取课程列表/章节列表/任务点卡片的 HTML 或内嵌 JS 变量并解析；
- 用"上报播放进度"的接口伪造视频/音频观看进度（`isdrag=4` 可直接尝试一次性完成）；
- 用题库或大模型回答章节检测，按覆盖率决定"保存"还是"提交"；
- 额外实现"章节学习次数"（访问章节 + 打点心跳 + 停留 30 秒）；
- 支持签到接口（但入口参数未接线，见 §5.2）。

因此它的核心资产是**对超星页面结构和接口参数的领域知识**，而不是通用架构能力。

---

## 3. 架构与运行链路

### 3.1 模块地图

```
main.py                      CLI、配置加载、课程筛选、任务调度（JobProcessor）、主流程
api/
  base.py                    Chaoxing 门面：登录、课程/章节、视频、文档、测验、直播、学习次数（1661 行）
  answer.py                  题库抽象与 7 种实现、答案缓存、手动答题（1938 行）
  decode.py                  课程/章节/任务卡片/题目 HTML 解析
  answer_check.py            答案类型判断与切分
  font_decoder.py            字体加密解码入口
  cxsecret_font.py           glyf 字形哈希 + 30884 条映射表还原字符
  cipher.py                  登录密码 AES-CBC 加密
  captcha.py                 ddddocr 验证码识别（403 时调用）
  cookies.py                 cookies.txt 读写
  config.py                  全局常量（AES key、UA、阈值）
  logger.py                  loguru + tqdm 兼容、手动答题日志缓冲
  notification.py            ServerChan/Qmsg/Bark/Telegram 通知
  live.py / live_process.py  直播时长上报
  process.py                 进度条辅助 + 章节学习次数编排
  exceptions.py              自定义异常
app.py                       Flask + Celery 骨架（主流程未使用）
tests/                       17 个 unittest 用例
resource/font_map_table.json 1.6MB 字体哈希映射表
```

### 3.2 主流程

```
CLI / config.ini
   │
   ├─ init_config()            配置文件与命令行二选一（不是合并）
   ├─ init_chaoxing()          Tiku 初始化 + 可选大模型连通性检查
   ├─ Chaoxing.login()         AES 加密登录 → cookies.txt → Session
   ├─ get_course_list()        课程列表 + 文件夹递归
   ├─ filter_courses()         按 courseId 筛选（无匹配则回退全量）
   ├─ get_course_point()       每门课的章节列表
   ├─ JobProcessor.run()       全局优先队列 + jobs 个工作线程 + 1 个重试线程
   │     └─ process_chapter()
   │           ├─ video   → ananas/status + multimedia/log（伪造进度；403 → 验证码/重登）
   │           ├─ document→ ananas/job/document
   │           ├─ read    → ananas/job/readv2
   │           ├─ workid  → api/work → decode_questions_info → Tiku.query_all
   │           │              → addStudentWorkNew → record-list/detail → 错题反馈 → 重做
   │           └─ live    → LiveProcessor（独立线程，阻塞 join）
   ├─ notification.send("所有课程学习任务已完成")
   └─ add_learning_count    → 访问章节 + setlog + monitor 心跳 + sleep(30)
```

### 3.3 值得肯定的设计

1. **领域知识沉淀充分**：字体加密（`cxsecret_font`）、视频/音频双模式回退、403 恢复、未开放章节、`isPassed=true` 但 `job=null` 的视频卡片、题库覆盖率、提交/仅保存双模式、手动答题、错题反馈重做——这些都不是"想当然"能写出来的，明显来自大量真实故障反馈。
2. **扩展点清晰**：`PROVIDER_REGISTRY` + `TikuFallback` 让新增题库成本较低；通知服务用模板方法组织。
3. **用户可见体验不错**：tqdm 进度、彩色日志、外部通知、配置模板注释完整、多 token 轮换、成本/覆盖率提示。
4. **近期有测试意识**：`tests/test_job_processor.py`、`tests/test_work_check.py` 是对真实回归（#612、成绩误判）的针对性测试；本次运行 17 项全部通过。
5. **没有把密钥提交进仓库**：`config.ini`、`cookies.txt`、`cache.json`、`chaoxing.log` 都在 `.gitignore` 中。

---

## 4. 代码质量度量

### 4.1 规模与复杂度

| 指标 | 值 | 评价 |
|---|---|---|
| Python 文件 / 行数 | 22 / 6317 | 中小型 |
| 最大文件 | `api/answer.py` 1938 行、`api/base.py` 1661 行、`main.py` 635 行 | 两个 God Class 占约 57% |
| Radon 圈复杂度 ≥ 11 | `study_work` **F(64)**、`_check_work_result` **E(37)**、`TikuLike._extract_answer_by_type` D(24)、`load_config_from_file` D(24)、`Tiku.get_tiku_from_config` C(18)、`study_video` C(18) | 测验链路是维护重灾区 |
| Radon 可维护性指数 | `answer.py` 0.00、`base.py` 0.00、`main.py` 26.76 | 前两者已到工具下限，说明"难以安全修改" |
| Ruff 0.17.2（默认规则集） | 198 条 | 其中 F/B/S 类 43 条：6 处无 timeout、4 处关闭 TLS 校验、6 处 `except: pass`、11 处普通随机数；其余多为 UP/E501 等风格建议 |
| Vulture | 仅报 `format_time` 的两个未用参数 | 死代码检测不充分（大量跨文件死代码未被识别） |

> 说明：Ruff 默认规则里相当一部分是风格/现代化建议，不等于 bug；`study_work` F(64) 这类则直接对应"改一处坏一片"的风险。

### 4.2 测试

| 项 | 现状 |
|---|---|
| 框架 | `unittest`（无 pytest、无 fixtures） |
| 用例数 | 17 |
| 本次结果 | 17 passed（Python 3.11 + 手工安装依赖；项目声明 3.13） |
| 覆盖范围 | `JobProcessor` 重试/未开放章节；成绩解析与答案归一化 |
| 未覆盖 | 登录/Cookie、课程/章节/任务卡片解析、视频进度、题库 provider、缓存并发、配置加载、通知、字体解密、CLI |
| CI 是否执行 | **否** |

结论：测试是"针对已发生 bug 的补丁"，不是"可支撑重构的安全网"。

### 4.3 依赖与打包

`requirements.txt` 共 16 项，存在明显问题：

- `argparse>=1.4.0`：**argparse 是标准库**，PyPI 上的 `argparse` 是 2015 年的废弃包，属于典型的错误依赖；
- `celery`、`flask`：仅被未接入主流程的 `app.py` 使用，普通用户白装；
- `chardet`：代码中**没有任何引用**；
- `openai` 在 `api/answer.py` 顶层导入：即使用户只用言溪题库，也必须安装 OpenAI SDK；
- `pyaes`：2015 年后无维护，且是自己手写 CBC 分块/填充；可换成 `cryptography` 或 `pycryptodome`；
- 绝大多数只有 `>=` 下界、无锁文件；README 推荐的 `uv` 而 `.gitignore` 又忽略了 `uv.lock`，可复现性差；
- `pyproject.toml` 没有 `[build-system]`，`py-modules = []` 意味着 `pip install .` **不会安装任何代码**（README 也仅把它当"装依赖"用），且没有 `console_scripts` 入口。

---

## 5. 缺陷与风险清单

> 标记说明：**【实测】** = 本次在沙箱中复现；**【静态】** = 代码路径确定但未联网；**【推断】** = 依赖真实页面/接口，需要 fixture 或实机确认。

### 5.1 P0：会破坏"无人值守"承诺的问题

#### P0-1 worker 异常导致主线程永久阻塞【实测】

- 位置：`main.py:376`（`task_queue.join()`）、`main.py:382-390`（worker 无 try/finally）
- 机制：`process_chapter()` 内任何未捕获异常（网络、解析、第三方题库）→ `@log_error` 记录后**重新抛出** → worker 线程退出 → 该任务从未 `task_done()` → `join()` 永不返回。
- 复现：

```
模拟 get_job_list 抛 RuntimeError
→ worker 抛异常后 run() 是否返回: False
→ unfinished_tasks: 1
```

- 影响：进程看起来"还在跑"，实际不再处理任何任务；Docker 容器不退出；用户收不到失败通知。
- 修复：worker 内 `try/except/finally`，异常映射为 `ChapterResult.ERROR`；确保每个 `get()` 恰好一次 `task_done()`；增加"全局看门狗"（总时长/无进展超时）与失败退出码。

#### P0-2 SessionManager 单例失效【实测】

- 位置：`api/base.py:39-58`
- 机制：`__new__` 返回单例，但 `__init__` 没有 `_initialized` 守卫，Python 每次 `SessionManager()` 都会重新执行 `self._session = requests.Session()`。
- 复现：

```
SessionManager 复用同一 session: False
cookies 对象同一: False
```

- 影响：
  - 每个请求新建连接池 → TLS 握手/连接开销显著增加；
  - 运行期通过验证码或重登更新的 Cookie 不在 `cookies.txt` 中 → 下一次 `get_session()` 丢失；
  - 多个 worker 共享一个 `requests.Session` 本身也不是线程安全的，重登/更新 Cookie 与请求并发会放大问题。
- 修复：真正的单例（模块级 session 或 `__new__` 中完成初始化），并为多线程使用**每线程 session** 或加锁的 cookie 合并策略。

#### P0-3 失败状态不传播，成功通知误报【静态 + 上游 #618】

- 位置：`main.py:466-468`（空任务点直接 `pass` → SUCCESS）、`main.py:322`（直播线程无论成败都返回 SUCCESS）、`main.py:606-607`（无条件发送"已完成"）、`JobProcessor.failed_tasks` 从未被主流程读取。
- 影响：章节没做完、直播没刷上、题库全挂，用户仍收到"所有课程学习任务已完成"；后续章节因解锁依赖失败而永远打不开。
- 修复：引入 `RunSummary(total/success/failed/skipped/not_open)`，`main()` 依据汇总决定通知文案与退出码；直播 `run_live` 返回值必须回传；空任务点要区分"确实无任务"和"读取失败"。

#### P0-4 课程列表无匹配 → 学习全部课程【静态 + 上游 #618】

- 位置：`main.py:531-533`
- 机制：

```python
if not course_task:
    course_task = all_course
```

- 触发条件：配置模板原样使用（`course_list = xxx,xxx,xxx`）、手滑打错课程 ID、输入为空/空白。
- 影响：本意只刷 1 门课，实际对账号下所有课程执行；这是"误操作"级别的问题。
- 修复：显式列表无匹配时**直接中止**并列出可用课程；只有明确输入 `*`/`all` 才全量。

#### P0-5 章节并发破坏学习顺序【静态 + 上游 #641/#588】

- 位置：`main.py:365-376`、`main.py:590-598`（每门课单独 `enumerate`，`index` 从 0 重新开始）
- 机制：`PriorityQueue` 只按 `ChapterTask.index` 排序；4 个 worker 启动后，先取到同一门课的第 0/1/2/3 章，**并行**处理。若第 2 章依赖第 1 章测验通过，就会得到 `NOT_OPEN` → 重试 → 消耗 5 次机会后放弃。
- 影响：大量"章节未开启"日志、无谓重试、章节检测顺序错乱；视频并发上报还可能造成服务端时长不累计（#641 的实机反馈）。
- 修复：调度维度改为"课程内串行、课程间并行"（至少提供开关）；`PriorityQueue` 的 key 使用 `(course_order, chapter_index)` 而不是裸 `index`；对有解锁依赖的课程强制串行。

### 5.2 P1：功能/正确性缺陷

| 编号 | 问题 | 证据 | 影响 |
|---|---|---|---|
| P1-1 | `notopen_action=ask` 未实现 | `main.py:399` 只处理 `continue`，其余一律重试；README/CLI 却宣称支持 `ask` | 文档与行为不一致，用户无法在关闭章节处人工决策 |
| P1-2 | `--auto-sign` 是死参数 | `main.py:93` 定义；`base.py:523-599` 的签到方法无任何调用方 | 用户以为能自动签到，实际完全没有执行 |
| P1-3 | 使用 `-c` 时命令行参数被完全忽略 | `main.py:204-208` 二选一 | `-c config.ini -j 1 -s 1.5` 不生效；`--use-cookies` 也被忽略 |
| P1-4 | 视频进度循环无最大尝试次数 | `api/base.py:894` `while not passed` | 服务端持续返回 200 + `isPassed=false` 时可能永久循环 |
| P1-5 | 第三方题库/通知请求缺少超时 | `api/answer.py:582`（言溪）、`1138`（TikuAdapter）；`api/notification.py:198/234/269/308` 四个 `requests.post` 均无 timeout | 连接挂起 → worker 永久占用 → 放大 P0-1 |
| P1-6 | 验证码提交判定基本失效 | `api/captcha.py:125-126` 用 `status_code == 302` 判断，但 `Session.get` 默认 `allow_redirects=True` | 除非响应没有 `Location`，否则永远拿不到 302 → 403 恢复链路形同虚设 |
| P1-7 | 答案清洗会剥掉首字母 | `api/base.py:162-171`；实测 `clean_res('Python') == ['ython']`、`clean_res('Java') == ['ava']` | 依赖"子序列匹配"侥幸命中；一旦选项文本相似，可能映射到错误选项 |
| P1-8 | 选项被按字符串排序 | `api/decode.py:468` `q_options.sort()` | 若 `aria-label` 不带 `A.` 前缀或使用非字母编号，选项顺序与字母错位 → 答案字母错误（需真实页面确认） |
| P1-9 | 题目缓存只用题干做 key | `api/answer.py:245`、`317` | 同题干不同选项（"以下哪个正确"类）会命中错误答案 |
| P1-10 | `Tiku.query()` 逻辑错误且无调用方 | `api/answer.py:243-264`：缓存未命中时查询结果被丢弃；feedback 分支 `cache_dao` 未定义（`257`） | 公开 API 不可用；一旦被调用会 `UnboundLocalError` |
| P1-11 | 每章节固定 7 次卡片请求 | `api/base.py:628` `for _possible_num in "0123456"` | 请求量/耗时/风控暴露面翻倍；且结果简单累加，若接口返回重叠集合会产生重复任务（需实测） |
| P1-12 | 空任务点/空章节被当成功 | `main.py:466-468`；`study_emptypage` 返回值被忽略 | 读取失败静默跳过，用户以为完成 |
| P1-13 | 缓存并发不安全 | `cache.json` 的 `RLock` 是**实例级**的，每个 `CacheDAO()` 各有一把锁 | 多 worker 并发读改写仍会丢条目；全量读写导致 O(n) 开销 |
| P1-14 | Cookie 明文写入日志 | `api/base.py:397` `logger.debug(f"Logged in with cookies: ...")`；文件 handler 级别为 TRACE | 使用 Cookie 登录时，完整 Cookie 会落盘到 `chaoxing.log`，等同于账号会话泄露 |
| P1-15 | 通知 URL（含 token/key）写入日志 | `api/notification.py:180/221/257/292` | Server酱 sendkey、Bark device key、Telegram bot token 出现在控制台与日志 |
| P1-16 | 多处 `verify=False` | `api/answer.py:590/902/1052/1145`；`api/captcha.py:89` | 题库 token、题目内容可被中间人截获/篡改 |
| P1-17 | 运行目录耦合 | `api/answer.py:141` 用 `os.getcwd()` 拼 `config.ini`；`api/cxsecret_font.py:31-49` 用 `os.path.abspath(".")`；`cookies.py` 固定相对路径 | 从其他目录执行 `python /path/main.py` 时，题库配置、字体表、Cookie 全部读不到 |
| P1-18 | 配置缺少校验与默认值合并 | `main.py:120-198`；`Tiku.init_tiku()` 直接取 `_conf['submit']`、`['cover_rate']`、`['true_list']` 等 | 用户删掉任一键即 `KeyError` 崩溃；CLI 与文件配置无法覆盖合并 |
| P1-19 | 章节学习次数耗时不可控 | `api/base.py:1601-1656`：每计 1 次 `sleep(30)`，默认 100 次/课程，按课程串行 | 单课程约 50 分钟，多课程数小时；不显示 ETA，不可中断恢复 |
| P1-20 | `ChapterResult.SUCCESS = 0,` 是元组 | `main.py:32` 多了逗号 | `.value` 类型不一致；当前靠 Enum 身份比较未出事，属隐性坏味道 |

### 5.3 P2：可维护性与体验问题

- **God Class**：`Chaoxing` 同时负责认证、抓取、解析编排、视频、测验、直播、学习次数；`Tiku` 家族 1900 行。
- **重复逻辑**：`process_course()`（`main.py:481`）与 `main()` 里的章节读取重复；`course_task` 在增加学习次数时又读一遍章节。
- **死代码**：`wait_queue`、`process_course`、`show_progress`、`app.py`（Flask/Celery 完全未接入）、`api/README.md` 仍称 captcha"未启用"（实际已接入 403 恢复）。
- **日志策略**：TRACE 级别写 10MB 轮转但无保留份数；原始 HTML 在 TRACE 中整段输出，包含课程/个人信息；`tqdm_sink` 通过反射读取 `TikuManual._manual_lock`，耦合脆弱。
- **异常处理**：47 处 `BLE001` 盲 `except`，6 处 `except: pass`；既掩盖问题，也让"失败→成功"的误报更难发现。
- **通知内容**：Telegram 使用 `parse_mode=HTML` 但未转义消息中的 `<`/`&`，异常栈可能发送失败。
- **跨平台**：PyInstaller 只构建 Windows；`resource_path` 对 Linux/macOS 源码运行依赖 CWD。
- **`openai` 顶层导入**：启动即加载较重的 SDK；离线/只用手动题库的用户被迫安装。
- **UI/编码**：Windows 控制台 emoji/颜色兼容性未处理；`tqdm.format_sizeof` 被全局替换。
- **数据模型**：任务/章节/课程全部用裸 `dict`，没有 TypedDict/dataclass，字段拼写错误只能运行时暴露。

---

## 6. 安全与隐私评估

### 6.1 凭据与数据流

| 数据 | 存储/传输 | 风险 |
|---|---|---|
| 超星账号密码 | `config.ini` 明文；登录时 AES 加密（密钥硬编码在 `api/config.py`） | 配置文件被读取/备份/截图即泄露；AES 密钥是协议公开值，不提供额外保护 |
| 登录 Cookie | `cookies.txt` 明文；`chaoxing.log` 可能在 DEBUG 下落盘 | Cookie 等价于账号会话，泄露即可冒用 |
| 题库 token / AI key | `config.ini` 明文；部分请求 `verify=False` | 中间人可截获；日志打印通知 URL 也会泄露 key |
| 题目与选项 | 发送给第三方题库/AI 服务 | 课程内容、题干中的姓名/学号等可能外泄；第三方隐私政策未知 |
| 答案缓存 | `cache.json` 明文 | 同机其他用户可读；题目内容沉淀 |
| 运行日志 | `chaoxing.log` | 含课程名、章节名、错题、Cookie、通知 URL |

### 6.2 供应链与构建

- `pyaes` 长期无维护，手写 CBC 分块 + PKCS#7；建议替换为成熟库。
- `argparse` 从 PyPI 安装是错误依赖；`chardet` 完全未使用。
- 依赖无锁/无哈希校验，CI 每次 `pip install` 都可能拿到新版本；没有 `pip-audit`/Dependabot/CodeQL。
- **Docker 没有 `.dockerignore`，`COPY . /app` 会把本地 `config.ini`、`cookies.txt`、`cache.json`、`chaoxing.log` 一并打进镜像层**。这是最容易被忽视的凭据泄露路径。
- Docker 以 root 运行，使用清华镜像源（境外构建易失败），基础镜像只固定 tag 不固定 digest。
- CI 只构建 Windows exe，不跑测试/lint；`actions/checkout@v3`、`setup-python@v3` 已过时；没有发布签名/校验和。

### 6.3 平台侧风险

- 伪造 `playingTime`、`isdrag=4` 瞬间完成、`monitor` 心跳、验证码 OCR、自动签到/答题，都是明确的反检测行为；平台升级风控后可能触发：
  - 进度清零/成绩作废；
  - 账号限流、封禁；
  - 学校层面的学术不端调查。
- 多章节并发（默认 4）会让同一账号同时"观看"多个视频，既可能造成时长不累计（#641），也更容易被风控识别。建议至少 `jobs=1~2`、`speed<=1.5`。
- 工具依赖未公开接口，平台一改版就会大面积失效；这不是"稳定性 bug"，而是架构性风险。

---

## 7. 合规与许可证

1. **平台条款**：超星/学校的用户协议通常禁止自动化刷课、代签、代答。README 的"仅用于学习讨论"免责声明**不能免除用户责任**，也不能约束平台处罚。
2. **学术诚信**：自动完成章节测验、AI 答题并提交，属于典型的学术不端风险；部分课程只允许作答一次，自动重做还会造成不可逆后果。
3. **GPL-3.0**：README 写"禁止盈利/不允许商业发布"，但 GPL 本身**允许商业分发**（前提是源码同样开源、保留许可证）。"禁止盈利"不是 GPL 的附加条款，实际约束以 LICENSE 为准；若要限制商业使用，应换许可证（但这与 GPL 生态不兼容）。
4. **数据保护**：把题目、账号 Cookie、课程信息发给第三方题库/AI，可能涉及个人信息与课程内容授权问题；若做多用户服务，需要隐私政策、数据最小化、删除机制。
5. **建议**：个人自用也要"知情、可控、可回滚"；不要把它包装成付费服务；对外分发二进制时附源码与许可证。

---

## 8. 工程化与 CI/CD

| 环节 | 现状 | 缺口 |
|---|---|---|
| 代码规范 | 无 ruff/flake8/mypy 配置；本次 Ruff 默认规则 198 条 | 无基线、无 pre-commit、无格式化 |
| 测试 | 17 个 unittest；CI 不跑 | 无网络 mock、无 HTML fixture、无覆盖率门槛 |
| CI | 仅 Windows PyInstaller 构建 | 无 Linux/macOS、无 Python 版本矩阵、无 lint/test/audit、无缓存、无 release |
| 打包 | `pyproject` 不安装代码；无入口点 | 不能 `pip install` 后直接运行；依赖从 requirements 动态读取 |
| 容器 | root、无 .dockerignore、镜像源硬编码、无健康检查 | 安全与可移植性差 |
| 可观测性 | loguru 文本日志 | 无结构化字段、无请求 ID、无运行摘要、无指标 |
| 文档 | 用户 README 较完整 | 无架构文档、无数据流/隐私说明、无 CONTRIBUTING/SECURITY/CHANGELOG；`api/README` 已过时 |
| 发布 | 无 tag、无 release（fork） | 版本号与产物不可追溯 |

---

## 9. 改进路线图（按投入产出排序）

### 阶段 0：止血（1–2 天，建议立刻做）

1. **修复调度器**（P0-1/P0-5）
   - worker 用 `try/except/finally` 包裹 `process_chapter`，异常→`ChapterResult.ERROR`；
   - 确保 `task_done()` 与 `get()` 一一对应；
   - 队列 key 改为 `(course_order, chapter_index)`，并提供 `--course-serial` 开关；
   - 增加"总时长/无进展"看门狗，超时后输出摘要并以非 0 退出。
2. **修复 SessionManager**（P0-2）
   - 单例初始化只执行一次；多线程使用 thread-local session 或在请求层加锁；
   - 重登/验证码后统一刷新 Cookie 与连接池。
3. **结果汇总与退出码**（P0-3）
   - `RunSummary` + 通知模板；有失败时发送"部分失败"并 `sys.exit(1)`；
   - 直播、空任务点、未开放章节分别计数。
4. **严格课程筛选**（P0-4）
   - 显式列表无匹配 → 打印可用课程并中止；`*` 才表示全部。
5. **安全小修**（P1-14/15/16）
   - 删除 Cookie 日志；通知 URL 脱敏（只保留域名/尾号）；
   - 所有第三方请求加 timeout；默认 `verify=True`。

### 阶段 1：可靠性与结构（1–2 周）

6. **拆分 `base.py`**：`session.py`（认证/会话）、`course.py`（课程/章节）、`video.py`、`work.py`（测验编排）、`live.py`、`learning_count.py`；单文件 ≤ 400 行。
7. **测验状态机**：`FETCH → ANSWER → SUBMIT → VERIFY → (REDO|DONE|FAIL)`，显式最大轮数、单次作答课程保护、幂等提交；把 `study_work` 从 F(64) 降下来。
8. **统一配置层**：dataclass + 校验 + 默认值；优先级"默认 < 文件 < CLI"；支持 `--dry-run`、`--yes`、`--jobs` 覆盖；缺失键给出可读错误。
9. **题库适配层**：`Provider` 元数据（是否需要 token、超时、重试、成本）、统一 timeout/退避、健康检查、token 脱敏；修复 `Tiku.query`/`clean_res`/选项排序/缓存 key（题干+选项+题型哈希）。
10. **路径与打包**：所有路径基于项目根/用户配置目录；`pip install` 提供 `chaoxing` 入口；删除 `argparse`/`chardet`，把 `openai` 改为懒加载，用 `cryptography` 替换 `pyaes`。

### 阶段 2：工程质量（1–2 个月）

11. **测试体系**：pytest + `responses`/`respx`；为课程列表、章节、卡片、题目、作答记录建立脱敏 HTML fixture；调度器用 fake clock；覆盖率门槛先定 50%，核心解析/调度 70%。
12. **CI**：ubuntu+windows × Python 3.11/3.12/3.13；`ruff check`、`ruff format --check`、`mypy`（新模块 strict）、`pip-audit`、`pytest`、PyInstaller 构建、artifact 校验和；PR 必跑。
13. **容器**：`.dockerignore`（排除 config/cookies/cache/log/.git）、非 root 用户、固定 digest、可选 PyPI 源、配置挂载校验、`--init`。
14. **可观测性**：日志脱敏过滤器、保留 7 天、运行摘要（成功/失败/耗时/题库命中率/成本）、可选 JSON 日志。
15. **释放与治理**：tag + CHANGELOG + release notes、SECURITY.md、CONTRIBUTING.md、issue 模板、依赖更新机器人。

### 阶段 3：产品决策（需要你先定方向）

- **如果继续做个人 CLI**：删掉 `app.py`/Celery/Flask，聚焦单机可靠性与成本；把"签到/自动答题"等高风险功能做成显式开关并默认关闭。
- **如果要做多用户服务**：当前架构不成立。需要：每用户独立会话/凭据加密存储、任务队列（现有 Celery 骨架可用）、配额与限流、审计日志、数据删除、合规评估、隔离执行环境。
- **如果要做研究/教学演示**：建议脱敏数据 + 只读模式，避免真实账号操作。

---

## 10. 如果现在就要用：最低限度建议

1. 不要在未修复 P0-1 的情况下"挂机一整晚"；至少加一个外部超时（`timeout 6h python main.py ...`）。
2. `jobs=1` 或 `2`，`speed<=1.5`；避免同一账号多视频并发。
3. 题库先用"仅保存不提交"（`submit=false`）+ 人工复核，确认答案质量后再考虑提交。
4. `chmod 600 config.ini cookies.txt cache.json`; 不要把目录同步到网盘/仓库；Docker 构建前清理这些文件。
5. 关闭 `-v`，定期删除 `chaoxing.log`；不要把日志发到公开 issue（含 Cookie/课程信息）。
6. 重要课程/只允许一次作答的课程，关闭 `work_redo_enabled`，或改用手动模式。
7. 使用前备份学习进度截图；出现"章节未开启"先人工检查前置测验，不要盲目重试。
8. 不要把账号密码/Cookie 交给第三方"代刷"服务；本项目本身也会把这些数据发给题库/AI。

---

## 11. 需要进一步确认的问题

以下结论依赖真实页面/接口，本次未联网验证，建议用脱敏 fixture 或测试账号确认：

1. `knowledge/cards` 的 `num=0..6` 是否返回互不重叠的集合？若是，`job_list` 会重复（对应 P1-11）。
2. `aria-label` 是否总带 `A.` 前缀？这决定 `q_options.sort()` 是否真的会错位（P1-8）。
3. `processVerify.ac` 的成功响应是否一定带 `Location`？这决定验证码链路是否完全失效（P1-6）。
4. `multimedia/log` 对 `isdrag=4` + 全时长的接受度，以及并发上报是否累计时长（#641）。
5. 章节卡片中 `notOpen` 的判定是否只依赖"章节未开放"文案；平台改版会直接击穿。
6. 各题库 provider 的返回格式/错误码是否会变化；当前解析大量依赖字符串匹配。

---

## 12. 附录：本次评估的可复现证据

### 12.1 运行测试

```bash
python -m venv /tmp/venv
/tmp/venv/bin/pip install requests pyaes beautifulsoup4 lxml loguru tqdm fonttools \
  openai httpx urllib3 chardet tenacity flask celery typing_extensions
/tmp/venv/bin/python -m unittest discover -s tests -v
# Ran 17 tests in 4.588s — OK
```

### 12.2 定向复现脚本（节选）

```python
from api.base import SessionManager, clean_res
import main, threading

s1, s2 = SessionManager.get_session(), SessionManager.get_session()
print("Session 复用:", s1 is s2)          # False
print(clean_res("Python"))                # ['ython']

class Boom:  # get_job_list 抛异常
    ...
proc = main.JobProcessor(Boom(), [task], cfg)
t = threading.Thread(target=proc.run, daemon=True); t.start(); t.join(4)
print("run() 返回:", not t.is_alive())     # False
print("unfinished:", proc.task_queue.unfinished_tasks)  # 1
```

### 12.3 静态扫描

```bash
ruff check . --statistics          # 198 findings
radon cc -s -n C main.py api/*.py  # study_work F(64), _check_work_result E(37)
radon mi -s main.py api/*.py       # answer.py 0.00, base.py 0.00
vulture --min-confidence 80 ...    # 仅 2 条
```

### 12.4 上游 issue 对照

- [#618 任务调度死锁、失败状态误报与 Session 重建问题](https://github.com/Samueli924/chaoxing/issues/618)：与 P0-1/3/4/5、P0-2 完全对应。
- [#641 章节并发计时异常](https://github.com/Samueli924/chaoxing/issues/641)：对应 P0-5。
- [#627 是否全部答对的判断逻辑有误](https://github.com/Samueli924/chaoxing/issues/627)：对应 `study_work`/`_check_work_result` 的高复杂度与解析脆弱性。
- [#645 AI 答题图片不能上传](https://github.com/Samueli924/chaoxing/issues/645)、[#620 视频上报 403](https://github.com/Samueli924/chaoxing/issues/620)：说明平台侧变更频繁，当前"字符串/正则解析"路线维护成本高。

---

## 13. 一句话总评

**领域知识很强、工程底座偏弱。** 如果目标是"个人偶尔用一次"，它已经够用；如果目标是"稳定无人值守"或"多人服务"，必须先修 P0 的调度/会话/结果传播问题，再补测试和 CI，否则每次平台改版或一次网络抖动都可能表现为"看起来在跑、实际早已卡死"。

至于合规：这是一个高风险工具，README 的免责声明不改变平台条款、学校纪律与当地法律的适用；使用前应明确知情并自担后果。
