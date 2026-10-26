# -*- coding: utf-8 -*-
"""RunControl / 调度器可靠性回归测试."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.control import RunControl, TaskCancelled  # noqa: E402
import main  # noqa: E402


class RunControlTest(unittest.TestCase):
    def test_sleep_returns(self):
        control = RunControl()
        start = time.monotonic()
        control.sleep(0.1)
        self.assertGreaterEqual(time.monotonic() - start, 0.09)

    def test_stop_interrupts_sleep(self):
        control = RunControl()
        start = time.monotonic()
        threading.Timer(0.1, control.stop).start()
        with self.assertRaises(TaskCancelled):
            control.sleep(30)
        self.assertLess(time.monotonic() - start, 2)

    def test_pause_blocks_until_resume(self):
        control = RunControl()
        control.pause("test")
        done = threading.Event()

        def worker():
            control.checkpoint()
            done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertFalse(done.wait(0.2))
        control.resume()
        self.assertTrue(done.wait(2))

    def test_pause_then_stop(self):
        control = RunControl()
        control.pause("test")
        thread = threading.Thread(target=lambda: control.checkpoint(), daemon=True)
        thread.start()
        time.sleep(0.05)
        control.stop()
        thread.join(2)
        self.assertFalse(thread.is_alive())


class DummyChaoxing:
    def __init__(self, job_info=None, exc=None):
        self.job_info = job_info or {}
        self.exc = exc
        self.rate_limiter = _NoopRateLimiter()
        self.cookies_path = None

    def get_job_list(self, course, point):
        if self.exc:
            raise self.exc
        return self.job_info.get("jobs", []), self.job_info.get("job_info", {})


class _NoopRateLimiter:
    def limit_rate(self, *args, **kwargs):
        return None


class SchedulerReliabilityTest(unittest.TestCase):
    def setUp(self):
        main.logger.remove()

    def _run(self, processor, timeout=5):
        thread = threading.Thread(target=processor.run, daemon=True)
        thread.start()
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "调度器未在超时内返回（可能死锁）")

    def test_worker_exception_does_not_hang(self):
        point = {"title": "章节", "has_finished": False}
        course = {"title": "课程"}
        task = main.ChapterTask(index=0, point=point, course=course, order_key=(0, 0))
        config = {"speed": 1.0, "jobs": 1, "notopen_action": "retry", "retry_interval": 0.01}
        processor = main.JobProcessor(DummyChaoxing(exc=RuntimeError("boom")), [task], config)
        processor.max_tries = 1
        self._run(processor)
        self.assertEqual(task.status, "failed")
        self.assertEqual(processor.summary()["failed"], 1)

    def test_stop_releases_queue(self):
        point = {"title": "章节", "has_finished": False}
        course = {"title": "课程"}
        task = main.ChapterTask(index=0, point=point, course=course, order_key=(0, 0))
        config = {"speed": 1.0, "jobs": 1, "notopen_action": "retry", "retry_interval": 0.01}
        processor = main.JobProcessor(DummyChaoxing(exc=RuntimeError("boom")), [task], config)
        processor.max_tries = 5

        thread = threading.Thread(target=processor.run, daemon=True)
        thread.start()
        time.sleep(0.1)
        processor.stop()
        thread.join(3)
        self.assertFalse(thread.is_alive())


class CourseFilterTest(unittest.TestCase):
    def setUp(self):
        main.logger.remove()

    def test_explicit_unknown_course_raises_instead_of_all(self):
        from api.exceptions import InputFormatError
        with self.assertRaises(InputFormatError):
            main.filter_courses(
                [{"courseId": "1", "clazzId": "2", "title": "A"}],
                ["999"],
                interactive=False,
            )

    def test_star_means_all(self):
        result = main.filter_courses(
            [{"courseId": "1", "clazzId": "2", "title": "A"}], ["*"], interactive=False
        )
        self.assertEqual(len(result), 1)

    def test_star_with_no_courses_returns_empty(self):
        # 账号没有任何课程时，* 表示"全部"，结果应为空而不是报错/静默假装成功
        self.assertEqual(main.filter_courses([], ["*"], interactive=False), [])

    def test_explicit_id_with_no_courses_has_clear_error(self):
        from api.exceptions import InputFormatError
        with self.assertRaises(InputFormatError) as ctx:
            main.filter_courses([], ["999"], interactive=False)
        self.assertIn("没有任何课程", str(ctx.exception))

    def test_non_interactive_requires_list(self):
        from api.exceptions import InputFormatError
        with self.assertRaises(InputFormatError):
            main.filter_courses([], [], interactive=False)


class SessionManagerTest(unittest.TestCase):
    def test_bound_session_is_reused(self):
        from api.base import SessionManager
        session = SessionManager.get_session()
        SessionManager.bind(session=session)
        self.assertIs(SessionManager.get_session(), session)


if __name__ == "__main__":
    unittest.main()
