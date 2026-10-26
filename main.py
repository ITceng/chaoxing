# -*- coding: utf-8 -*-
import argparse
import configparser
import enum
import sys
import threading
import traceback
from dataclasses import dataclass
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Account, Chaoxing, SessionManager, StudyResult
from api.control import RunControl, TaskCancelled
from api.exceptions import InputFormatError, LoginError
from api.live import Live
from api.live_process import LiveProcessor
from api.logger import logger, set_account_context
from api.notification import Notification
from api.process import increase_learning_count_for_course

try:
    from queue import Empty, PriorityQueue, ShutDown
except ImportError:
    from queue import Empty, PriorityQueue

    class ShutDown(Exception):
        pass


class ChapterResult(enum.Enum):
    SUCCESS = 0
    ERROR = 1
    NOT_OPEN = 2
    PENDING = 3
    CANCELLED = 4


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as e:
            logger.error(f"Error in thread {threading.current_thread().name}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Samueli924/chaoxing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=None, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=None, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry",
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )

    parser.add_argument("--auto-sign", action="store_true", help="自动签到（当前版本尚未实现）")
    parser.add_argument(
        "--retry-interval", type=float, default=None, help="重试等待时间, 单位秒 (默认1.0)"
    )
    parser.add_argument(
        "--course-serial",
        dest="course_serial",
        action="store_true",
        default=None,
        help="课程内章节串行、课程之间并行（默认，推荐）",
    )
    parser.add_argument(
        "--course-parallel",
        dest="course_serial",
        action="store_false",
        help="课程内章节也并发（旧行为，可能破坏章节解锁顺序）",
    )

    parser.add_argument(
        "-lc",
        "--add-learning-count",
        action="store_true",
        help="开启章节学习次数增加模式",
    )
    parser.add_argument(
        "-tc",
        "--target-count",
        type=int,
        default=None,
        help="章节学习次数目标总次数 (默认100)",
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")

    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}

    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if
                                            item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        if "retry_interval" in common_config:
            common_config["retry_interval"] = float(common_config["retry_interval"])
        else:
            common_config["retry_interval"] = 1.0
        if "work_max_retries" in common_config:
            try:
                common_config["work_max_retries"] = int(common_config["work_max_retries"])
            except ValueError:
                common_config["work_max_retries"] = 3
        else:
            common_config["work_max_retries"] = 3
        if "work_redo_enabled" in common_config:
            common_config["work_redo_enabled"] = str_to_bool(common_config["work_redo_enabled"])
        else:
            common_config["work_redo_enabled"] = False
        if "course_serial" in common_config:
            common_config["course_serial"] = str_to_bool(common_config["course_serial"])
        else:
            common_config["course_serial"] = True
        if "cookies_path" in common_config:
            common_config["cookies_path"] = common_config["cookies_path"].strip() or None
        if "use_cookies" in common_config:
            common_config["use_cookies"] = str_to_bool(common_config["use_cookies"])
        if "add_learning_count" in common_config:
            common_config["add_learning_count"] = str_to_bool(common_config["add_learning_count"])
        if "target_count" in common_config:
            common_config["target_count"] = int(common_config["target_count"])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))

    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed or 1.0,
        "jobs": args.jobs or 4,
        "notopen_action": args.notopen_action or "retry",
        "retry_interval": args.retry_interval if args.retry_interval is not None else 1.0,
        "add_learning_count": args.add_learning_count,
        "target_count": args.target_count if args.target_count is not None else 100,
        "course_serial": args.course_serial if args.course_serial is not None else True,
        "work_redo_enabled": False,
        "work_max_retries": 3,
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()

    if args.config:
        common_config, tiku_config, notification_config = load_config_from_file(args.config)
        # 显式传入的命令行参数覆盖配置文件（配置文件仍是主体）
        if args.username:
            common_config["username"] = args.username
        if args.password:
            common_config["password"] = args.password
        if args.list:
            common_config["course_list"] = [item.strip() for item in args.list.split(",") if item.strip()]
        if args.speed is not None:
            common_config["speed"] = args.speed
        if args.jobs is not None:
            common_config["jobs"] = args.jobs
        if args.notopen_action:
            common_config["notopen_action"] = args.notopen_action
        if args.retry_interval is not None:
            common_config["retry_interval"] = args.retry_interval
        if args.add_learning_count:
            common_config["add_learning_count"] = True
        if args.target_count is not None:
            common_config["target_count"] = args.target_count
        if args.course_serial is not None:
            common_config["course_serial"] = args.course_serial
        if args.use_cookies:
            common_config["use_cookies"] = True
    else:
        common_config, tiku_config, notification_config = build_config_from_args(args)
    return common_config, tiku_config, notification_config, args.config


def init_chaoxing(common_config, tiku_config, config_path=None):
    """初始化超星实例"""
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)

    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        if common_config.get("non_interactive"):
            raise LoginError("账号或密码为空，且当前为非交互模式")
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")

    account = Account(username, password)

    # 设置题库
    tiku = Tiku.get_tiku_from_config(tiku_config, config_path=config_path)  # 载入题库
    tiku.init_tiku()  # 初始化题库

    # 获取查询延迟设置

    # 检查大模型连接（如果使用的是大模型题库）
    # 根据配置文件中的 provider 判断是否为大模型题库
    provider = tiku_config.get('provider', '')
    provider_list = [name.strip() for name in provider.split(',') if name.strip()]
    if any(name in ['AI', 'SiliconFlow'] for name in provider_list):
        check_connection = tiku_config.get('check_llm_connection', 'true').lower() == 'true'
        if check_connection:
            logger.info(f'正在验证大模型配置 (provider={provider})...')
            if not tiku.check_llm_connection():
                logger.error('大模型连接检查失败')
                if common_config.get("non_interactive"):
                    raise RuntimeError("大模型连接检查失败，已终止运行")
                choice = input('大模型连接检查失败，无法准确答题，是否继续运行？(Y/n): ').strip().lower()
                # 直接回车默认继续运行
                if choice not in ('', 'y', 'yes'):
                    raise RuntimeError('用户取消运行')
                logger.info('用户选择继续运行...')

    query_delay = tiku_config.get("delay", 0)

    # 章节检测答错后允许的最大重做次数（答错时反馈给AI并重新提交，直到全部正确）
    work_max_retries = common_config.get("work_max_retries", 3)

    # 是否允许自动重做(默认false: 仅检查并记录成绩, 适用于只允许作答一次的课程)
    work_redo_enabled = common_config.get("work_redo_enabled", False)

    # 实例化超星API
    chaoxing = Chaoxing(
        account=account,
        tiku=tiku,
        query_delay=query_delay,
        work_max_retries=work_max_retries,
        work_redo_enabled=work_redo_enabled,
        control=common_config.get("control"),
        cookies_path=common_config.get("cookies_path"),
        account_id=common_config.get("account_id"),
    )

    return chaoxing


def process_job(chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float) -> StudyResult:
    """处理单个任务点."""
    chaoxing.control.checkpoint()
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video"
        )
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio")
        if video_result.is_failure():
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
        return video_result
    # 文档任务
    if job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)
    # 测验任务
    if job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)
    # 阅读任务
    if job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_read(course, job, job_info)
    # 直播任务
    if job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        try:
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid"),
            }
            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
                control=chaoxing.control,
            )
            # 直接在 worker 线程中执行，避免额外的线程与 join 阻塞
            ok = LiveProcessor.run_live(live, speed, control=chaoxing.control)
            return StudyResult.SUCCESS if ok else StudyResult.ERROR
        except TaskCancelled:
            raise
        except Exception as e:
            logger.error(f"处理直播任务时出错: {e}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


@dataclass
class ChapterTask:
    index: int
    point: dict[str, Any]
    course: dict[str, Any]
    result: ChapterResult = ChapterResult.PENDING
    tries: int = 0
    order_key: tuple = ()
    status: str = "pending"  # pending / running / success / failed / skipped / cancelled
    error: str = ""

    def __lt__(self, other):
        """比较任务顺序，用于优先级队列排序."""
        if not isinstance(other, ChapterTask):
            return NotImplemented
        return (self.order_key or (self.index,)) < (other.order_key or (other.index,))


class JobProcessor:
    """章节任务调度器.

    - ``course_serial=True``：课程内章节串行、课程之间并行（默认，推荐）；
    - ``course_serial=False``：所有章节按章节下标全局并发（旧行为）。
    - 任意 worker 异常都会被转换成任务失败，绝不会让 ``join()`` 永久阻塞。
    """

    def __init__(self, chaoxing: Chaoxing, tasks: list[ChapterTask], config: dict[str, Any],
                 control: RunControl | None = None):
        if "jobs" not in config or not config["jobs"]:
            config["jobs"] = 4

        self.chaoxing = chaoxing
        self.speed = config.get("speed", 1.0)
        # 章节级重试次数（与章节检测的 work_max_retries 无关）
        self.max_tries = int(config.get("chapter_max_tries", 5) or 5)
        if self.max_tries <= 0:
            self.max_tries = 5
        self.tasks = tasks
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = max(1, int(config["jobs"]))
        self.config = config
        self.retry_interval = float(config.get("retry_interval", 1.0) or 0)
        self.course_serial = bool(config.get("course_serial", True))
        self.control = control or getattr(chaoxing, "control", None) or RunControl()
        self.stats = {
            "total": len(tasks),
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "not_open": 0,
            "cancelled": 0,
        }
        self._stats_lock = threading.Lock()
        self._stopped = False
        self._stop_sentinel = ChapterTask(
            index=-1, point={"title": "__stop__"}, course={"title": "__stop__"},
            order_key=(-1,),
        )

    # ------------------------------------------------------------------ stats
    def _bump(self, key: str, value: int = 1) -> None:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + value

    def summary(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self.stats)

    # --------------------------------------------------------------- lifecycle
    def run(self):
        for task in self.tasks:
            self.task_queue.put(task)

        for _ in range(self.worker_num):
            thread = threading.Thread(target=self.worker_thread, daemon=True,
                                      name=f"chapter-worker-{_}")
            self.threads.append(thread)
            thread.start()

        threading.Thread(target=self.retry_thread, daemon=True, name="chapter-retry").start()

        try:
            self.task_queue.join()
        finally:
            if hasattr(self.task_queue, "shutdown"):
                self.task_queue.shutdown()
        return self.summary()

    def stop(self):
        """停止调度：唤醒所有检查点并清空队列，保证 join() 能返回."""
        self._stopped = True
        self.control.stop()
        self._drain(self.task_queue, accounting=True)
        # retry_queue 中的任务由 worker 暂缓 task_done，需要在这里补齐
        # 注意：task_done 必须作用在 task_queue 上，否则 join() 不会返回。
        while True:
            try:
                self.retry_queue.get_nowait()
            except (Empty, ShutDown):
                break
            try:
                self.task_queue.task_done()
            except ValueError:
                break
        # 唤醒阻塞在 get() 上的 worker（Python < 3.13 没有 Queue.shutdown）
        for _ in range(self.worker_num):
            self.task_queue.put(self._stop_sentinel)

    @staticmethod
    def _drain(queue, accounting: bool):
        while True:
            try:
                _task = queue.get_nowait()
            except (Empty, ShutDown):
                return
            if accounting:
                queue.task_done()
            else:
                try:
                    if hasattr(queue, "task_done"):
                        # retry_queue 中的任务不计入 task_queue 的 unfinished 计数
                        pass
                except Exception:
                    pass

    # ------------------------------------------------------------------ worker
    def _bind_thread_session(self):
        try:
            account_id = getattr(self.chaoxing, "account_id", None) or self.config.get("account_id")
            if account_id:
                set_account_context(str(account_id))
            if hasattr(self.chaoxing, "new_session"):
                SessionManager.bind(
                    session=self.chaoxing.new_session(),
                    cookies_path=getattr(self.chaoxing, "cookies_path", None),
                )
            elif hasattr(self.chaoxing, "cookies_path"):
                SessionManager.bind(cookies_path=getattr(self.chaoxing, "cookies_path", None))
        except Exception as e:
            logger.debug(f"绑定线程会话失败，将使用默认会话: {e}")

    def worker_thread(self):
        self._bind_thread_session()
        while True:
            try:
                task = self.task_queue.get()
            except ShutDown:
                logger.info("Queue shut down")
                return

            if task is self._stop_sentinel:
                self.task_queue.task_done()
                return

            if self.control.stopped:
                self.task_queue.task_done()
                return

            task.status = "running"
            try:
                task.result = process_chapter(self.chaoxing, task.course, task.point, self.speed)
            except TaskCancelled:
                task.result = ChapterResult.CANCELLED
                task.error = "cancelled"
            except BaseException as e:  # noqa: BLE001 - 线程顶层必须兜底，否则 join 永久阻塞
                logger.error(f"章节处理异常 [{task.course.get('title')} - {task.point.get('title')}]: "
                             f"{type(e).__name__}: {e}")
                logger.debug(traceback.format_exc())
                task.result = ChapterResult.ERROR
                task.error = f"{type(e).__name__}: {e}"

            requeued = False
            try:
                requeued = self._handle_result(task)
            except Exception as e:  # noqa: BLE001
                logger.error(f"处理任务结果时异常: {e}")
                task.status = "failed"
                self._bump("failed")
                self.failed_tasks.append(task)
            finally:
                # 需要重试的任务在 retry 线程重新入队后再 task_done，
                # 否则 join() 会在重试任务真正入队前提前返回。
                if not requeued:
                    self.task_queue.task_done()

    def _handle_result(self, task: ChapterTask) -> bool:
        """处理任务结果；返回 True 表示已放入 retry_queue（由 retry 线程负责计数）."""
        match task.result:
            case ChapterResult.SUCCESS:
                task.status = "success"
                self._bump("success")
                logger.debug("Task success: {} - {}", task.course["title"], task.point["title"])
                logger.debug(f"unfinished task: {self.task_queue.unfinished_tasks}")
                return False

            case ChapterResult.CANCELLED:
                task.status = "cancelled"
                self._bump("cancelled")
                self.failed_tasks.append(task)
                return False

            case ChapterResult.NOT_OPEN:
                if self.control.stopped:
                    task.status = "cancelled"
                    self._bump("cancelled")
                    return False
                if self.config.get("notopen_action") == "continue":
                    logger.warning("章节未开启: {} - {}, 正在跳过", task.course["title"], task.point["title"])
                    task.status = "skipped"
                    self._bump("skipped")
                    self._bump("not_open")
                    return False
                if self.config.get("notopen_action") == "ask":
                    logger.warning("章节未开启: {} - {}", task.course["title"], task.point["title"])
                    task.status = "skipped"
                    self._bump("skipped")
                    self._bump("not_open")
                    return False

                task.tries += 1
                if task.tries >= self.max_tries:
                    logger.error(
                        "章节未开启: {} - {} 可能由于上一章节的章节检测未完成, 也可能由于该章节因为时效已关闭，"
                        "请手动检查完成并提交再重试。或者配置 notopen_action=continue 跳过。",
                        task.course["title"], task.point["title"],
                    )
                    task.status = "skipped"
                    self._bump("skipped")
                    self._bump("not_open")
                    return False
                task.status = "pending"
                self.retry_queue.put(task)
                return True

            case ChapterResult.ERROR:
                task.tries += 1
                logger.warning("重试任务 {} - {} ({}/{} 次尝试)", task.course["title"], task.point["title"],
                               task.tries, self.max_tries)
                if task.tries >= self.max_tries or self.control.stopped:
                    logger.error("任务重试次数达到上限: {} - {}", task.course["title"], task.point["title"])
                    task.status = "failed"
                    self._bump("failed")
                    self.failed_tasks.append(task)
                    return False
                task.status = "pending"
                self.retry_queue.put(task)
                return True

            case _:
                logger.error("任务 {} 的状态无效 {}", task.result, task.point["title"])
                task.status = "failed"
                self._bump("failed")
                self.failed_tasks.append(task)
                return False

    def retry_thread(self):
        while True:
            if self.control.stopped:
                return
            try:
                task = self.retry_queue.get()
            except ShutDown:
                return
            if task is self._stop_sentinel:
                return
            if self.control.stopped:
                task.status = "cancelled"
                task.result = ChapterResult.CANCELLED
                self._bump("cancelled")
                self.task_queue.task_done()
                continue
            self.task_queue.put(task)
            self.task_queue.task_done()
            try:
                self.control.sleep(self.retry_interval)
            except TaskCancelled:
                return


def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any],
                    speed: float) -> ChapterResult:
    """处理单个章节."""
    control = getattr(chaoxing, "control", None) or RunControl()
    control.checkpoint()
    logger.info(f'当前章节: {point["title"]}')
    if point["has_finished"]:
        logger.info(f'章节：{point["title"]} 已完成所有任务点')
        return ChapterResult.SUCCESS

    # 随机等待，避免请求过快
    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    control.checkpoint()

    # 获取当前章节的所有任务点
    jobs, job_info = chaoxing.get_job_list(course, point)

    # 发现未开放章节, 根据配置处理
    if job_info.get("notOpen", False):
        return ChapterResult.NOT_OPEN

    if not jobs:
        # get_job_list 在空页面时会调用 study_emptypage，这里读取其结果，
        # 避免把"读取失败/空页面失败"误判为成功。
        empty_result = job_info.get("empty_page_result")
        if empty_result == "ERROR":
            logger.error(f"章节为空且空页面任务失败: {point['title']}")
            return ChapterResult.ERROR
        logger.warning(f"章节没有可执行任务点: {point['title']}")
        return ChapterResult.SUCCESS

    job_results: list[StudyResult] = []
    for job in jobs:
        control.checkpoint()
        result = process_job(chaoxing, course, job, job_info, speed)
        job_results.append(result)

    for result in job_results:
        if result.is_failure():
            return ChapterResult.ERROR

    return ChapterResult.SUCCESS


def process_course(chaoxing: Chaoxing, course: dict[str, Any], config: dict):
    """处理单个课程（保留兼容入口，内部复用 JobProcessor）."""
    logger.info(f"开始学习课程: {course['title']}")
    point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
    tasks = [ChapterTask(point=point, index=i, course=course, order_key=(i,))
             for i, point in enumerate(point_list["points"])]
    processor = JobProcessor(chaoxing, tasks, config)
    return processor.run()


def filter_courses(all_course, course_list, interactive: bool | None = None):
    """过滤要学习的课程.

    - 显式指定了课程但没有任何匹配时直接报错，绝不退化为"学习全部课程"；
    - 只有用户明确输入 ``*``（或 ``all``）才表示全部课程；
    - 非交互环境（面板/服务器）不会阻塞在 input() 上。
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    if isinstance(course_list, str):
        course_list = [item.strip() for item in course_list.split(",") if item.strip()]

    if not course_list:
        if not interactive:
            raise InputFormatError(
                "未指定课程列表，且当前不是交互式终端。请使用 -l 指定课程 ID，或输入 * 表示全部课程。"
            )
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 班级ID: {course['clazzId']} 课程名: {course['title']}")
        print("*" * 28)
        print("提示: 同一 courseId 下若存在多个班级, 将分别完成。")
        print("提示: 输入 * 表示学习全部课程。")
        try:
            course_list = [item.strip() for item in input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198\n"
            ).split(",") if item.strip()]
        except EOFError as e:
            raise InputFormatError("未读取到课程列表输入") from e

    want_all = any(item in {"*", "all", "ALL"} for item in course_list)
    if want_all:
        if not all_course:
            logger.warning("账号下没有任何课程（可能未选课或登录状态异常），没有可学习的课程")
            return []
        course_list = [course["courseId"] for course in all_course]

    course_task = []
    seen_keys = set()
    for course in all_course:
        key = (course["courseId"], course["clazzId"])
        if course["courseId"] in course_list and key not in seen_keys:
            course_task.append(course)
            seen_keys.add(key)

    if not course_task:
        if not all_course:
            raise InputFormatError(
                f"账号下没有任何课程，无法匹配指定课程 ID: {','.join(map(str, course_list))}"
            )
        available = ", ".join(f"{c['courseId']}({c['title']})" for c in all_course[:20])
        raise InputFormatError(
            f"指定的课程 ID 在账号下不存在: {','.join(map(str, course_list))}。可用课程: {available}"
        )

    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def build_chapter_tasks(chaoxing: Chaoxing, course_task: list[dict], course_serial: bool = True):
    """读取章节并构建任务列表；同一课程的 order_key 保证串行."""
    tasks = []
    for course_order, course in enumerate(course_task):
        logger.info(f"正在读取课程章节: {course['title']}")
        point_list = chaoxing.get_course_point(
            course["courseId"], course["clazzId"], course["cpi"]
        )
        for i, point in enumerate(point_list["points"]):
            order_key = (course_order, i) if course_serial else (i, course_order)
            tasks.append(ChapterTask(point=point, index=i, course=course, order_key=order_key))
    return tasks


def main():
    """主程序入口（CLI 单账号）."""
    notification = None
    control = RunControl()
    try:
        # 初始化配置
        common_config, tiku_config, notification_config, config_path = init_config()
        common_config["control"] = control

        # 强制播放按照配置文件调节
        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry")

        # 初始化增加章节学习次数配置
        add_learning_count = str_to_bool(common_config.get("add_learning_count", False))
        target_count = int(common_config.get("target_count", 100))

        # 绑定 Cookie 文件（多账号/面板时由上层指定）
        SessionManager.bind(cookies_path=common_config.get("cookies_path"))

        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config, config_path=config_path)

        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()

        # 检查当前登录状态
        _login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])

        # 获取所有的课程列表
        all_course = chaoxing.get_course_list()

        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"))

        # 开始学习
        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")

        _old_format_sizeof = tqdm.format_sizeof
        tqdm.format_sizeof = format_time

        tasks = build_chapter_tasks(
            chaoxing, course_task, course_serial=bool(common_config.get("course_serial", True))
        )

        p = JobProcessor(chaoxing, tasks, common_config, control=control)
        summary = p.run()

        tqdm.format_sizeof = _old_format_sizeof

        if control.stopped:
            logger.warning(f"任务已被用户停止: {summary}")
            if notification:
                notification.send(f"chaoxing : 任务已停止 {summary}")
            return 2

        failed = summary["failed"] + summary["cancelled"]
        if failed:
            message = (f"chaoxing : 任务结束，但有 {failed} 个章节失败，"
                       f"成功 {summary['success']}，跳过 {summary['skipped']}")
            logger.error(message)
            if notification:
                notification.send(message)
            return 1

        logger.info("所有课程学习任务已完成")
        if notification:
            notification.send("chaoxing : 所有课程学习任务已完成")

        # 刷课完成后，如果开启了增加章节学习次数，则执行
        if add_learning_count:
            logger.info("刷课完成，开始增加章节学习次数...")
            common_config["target_count"] = target_count
            for course in course_task:
                control.checkpoint()
                increase_learning_count_for_course(chaoxing, course, common_config)
            logger.info("所有课程章节学习次数增加完成")
            if notification:
                notification.send("chaoxing : 所有课程章节学习次数增加完成")
        return 0

    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt:
        control.stop()
        logger.error("错误: 程序被用户手动中断")
        return 130
    except InputFormatError as e:
        # 参数/课程列表等输入问题：只给一行提示，不打印 traceback，方便脚本按返回码判断
        logger.error(f"输入错误: {e}")
        return 2
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        if notification:
            try:
                notification.send(f"chaoxing : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    sys.exit(main())
