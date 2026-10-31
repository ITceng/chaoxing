(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const loginView = $("login-view");
  const dashboard = $("dashboard");
  const loginBadge = $("login-badge");
  const logoutBtn = $("logout-btn");
  const toastEl = $("toast");

  const accountInput = $("account");
  const passwordInput = $("password");
  const passwordForm = $("password-form");
  const passwordSubmit = $("password-submit");
  const passwordDirect = $("password-direct");

  const qrStart = $("qr-start");
  const qrBox = $("qr-box");
  const qrImg = $("qr-img");
  const qrPlaceholder = $("qr-placeholder");
  const qrStatus = $("qr-status");

  const cookieInput = $("cookie-input");
  const cookieImport = $("cookie-import");

  const refreshCoursesBtn = $("refresh-courses");
  const courseList = $("course-list");
  const courseCount = $("course-count");
  const selectAll = $("select-all");

  const speedInput = $("speed");
  const thresholdInput = $("threshold");
  const limitInput = $("limit");
  const videosInput = $("videos");
  const treeViewInput = $("tree-view");
  const progressbarInput = $("progressbar-view");

  const startSelected = $("start-selected");
  const startAll = $("start-all");
  const stopTask = $("stop-task");
  const taskBadge = $("task-badge");

  const logEl = $("log");
  const clearLogBtn = $("clear-log");

  const progressBar = $("progress-bar");
  const progressPercent = $("progress-percent");
  const progressCourse = $("progress-course");
  const progressVideo = $("progress-video");
  const progressDetail = $("progress-detail");

  let loggedIn = false;
  let coursesLoaded = false;
  let configApplied = false;
  let qrSid = null;
  let qrTimer = null;
  let lastLogId = 0;
  let toastTimer = null;
  let captchaInstance = null;

  // ------------------------------------------------------------------
  // 基础工具
  // ------------------------------------------------------------------
  async function api(path, options = {}) {
    const opts = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(options.body);
    }
    try {
      const response = await fetch(path, opts);
      let data = {};
      try {
        data = await response.json();
      } catch (e) {
        data = { ok: false, message: `HTTP ${response.status}` };
      }
      if (data.ok === undefined) {
        data.ok = false;
        if (!data.message) data.message = `HTTP ${response.status}`;
      }
      return data;
    } catch (e) {
      return { ok: false, message: `网络错误：${e.message}` };
    }
  }

  function toast(message, type = "") {
    toastEl.textContent = message;
    toastEl.className = "toast" + (type ? " " + type : "");
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 4200);
  }

  function setBadge(text, cls) {
    loginBadge.textContent = text;
    loginBadge.className = "badge " + cls;
  }

  function setTaskBadge(running, stopRequested) {
    if (running) {
      taskBadge.textContent = stopRequested ? "停止中" : "运行中";
      taskBadge.className = "badge badge-busy";
    } else {
      taskBadge.textContent = "空闲";
      taskBadge.className = "badge badge-off";
    }
    startSelected.disabled = running;
    startAll.disabled = running;
    stopTask.disabled = !running;
  }

  function escapeText(text) {
    const span = document.createElement("span");
    span.textContent = text;
    return span;
  }

  // ------------------------------------------------------------------
  // Tab 切换
  // ------------------------------------------------------------------
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      const panel = $("tab-" + tab.dataset.tab);
      if (panel) panel.classList.add("active");
    });
  });

  // ------------------------------------------------------------------
  // 滑动验证码
  // ------------------------------------------------------------------
  const captchaHint = $("captcha-hint");

  function updateCaptchaHint() {
    if (!captchaHint) return;
    if (typeof window.AbleCaptcha !== "undefined") {
      captchaHint.dataset.ready = "1";
      captchaHint.textContent = "验证码组件已加载，点击登录后会弹出滑动验证。";
    } else {
      captchaHint.dataset.ready = "0";
      captchaHint.textContent = "验证码组件加载失败，可点击下方“直接尝试登录”。";
    }
  }
  setTimeout(updateCaptchaHint, 500);
  setTimeout(updateCaptchaHint, 2500);

  function getValidate() {
    return new Promise((resolve, reject) => {
      if (typeof window.AbleCaptcha === "undefined") {
        reject(new Error("验证码组件加载失败，请检查网络后刷新页面"));
        return;
      }
      if (captchaInstance) {
        try { captchaInstance.destroy(); } catch (e) { /* ignore */ }
        captchaInstance = null;
      }
      let settled = false;
      const finish = (fn, value) => {
        if (settled) return;
        settled = true;
        try {
          if (captchaInstance && captchaInstance.destroy) captchaInstance.destroy();
        } catch (e) { /* ignore */ }
        captchaInstance = null;
        fn(value);
      };

      try {
        captchaInstance = new window.AbleCaptcha({
          source: 1,
          capType: "common",
          eleCapId: "#ableCaptchaEl",
          width: "400px",
          popupTitle: "安全验证",
          enableClose: 0,
          errorFallbackCount: 3,
          defaultFallback: false,
          onSuccessAble: (source, error, result) => {
            const validate = result && result.data && result.data.validate;
            if (validate) finish(resolve, validate);
            else finish(reject, new Error("未获取到验证码 token"));
          },
          onErrorAble: (error) => {
            finish(reject, new Error("验证码加载失败：" + (error && error.message ? error.message : error)));
          },
          onLoadAble: (instance) => {
            try {
              (instance || captchaInstance).popUp();
            } catch (e) {
              finish(reject, e);
            }
          },
          onCloseAble: () => finish(reject, new Error("已取消验证")),
          onFallbackAble: () => finish(reject, new Error("验证码降级失败，请重试")),
        });
      } catch (e) {
        finish(reject, e);
      }
    });
  }

  // ------------------------------------------------------------------
  // 账号密码登录
  // ------------------------------------------------------------------
  async function doPasswordLogin(useCaptcha) {
    const account = accountInput.value.trim();
    const password = passwordInput.value;
    if (!account || !password) {
      toast("请输入账号和密码", "error");
      return;
    }
    passwordSubmit.disabled = true;
    passwordDirect.disabled = true;
    try {
      let validate = "";
      if (useCaptcha) {
        passwordSubmit.textContent = "请完成滑动验证...";
        validate = await getValidate();
      }
      passwordSubmit.textContent = "登录中...";
      const result = await api("/api/login/password", {
        method: "POST",
        body: { account, password, validate },
      });
      if (result.ok) {
        toast("登录成功", "success");
        passwordInput.value = "";
        coursesLoaded = false;
        await refreshStatus(true);
      } else {
        toast(result.message || "登录失败", "error");
        if (result.need_captcha) {
          passwordSubmit.textContent = "重新验证并登录";
          setTimeout(() => { passwordSubmit.textContent = "登录（含滑动验证）"; }, 3000);
        }
      }
    } catch (e) {
      toast(e.message || String(e), "error");
    } finally {
      passwordSubmit.disabled = false;
      passwordDirect.disabled = false;
      if (passwordSubmit.textContent === "请完成滑动验证..." || passwordSubmit.textContent === "登录中...") {
        passwordSubmit.textContent = "登录（含滑动验证）";
      }
    }
  }

  passwordForm.addEventListener("submit", (event) => {
    event.preventDefault();
    doPasswordLogin(true);
  });
  passwordDirect.addEventListener("click", () => doPasswordLogin(false));

  // ------------------------------------------------------------------
  // 二维码登录
  // ------------------------------------------------------------------
  function stopQrPolling() {
    if (qrTimer) {
      clearInterval(qrTimer);
      qrTimer = null;
    }
    qrSid = null;
  }

  async function startQrLogin() {
    stopQrPolling();
    qrStart.disabled = true;
    qrStatus.textContent = "正在获取二维码...";
    try {
      const result = await api("/api/login/qr/start", { method: "POST" });
      if (!result.ok) {
        qrStatus.textContent = result.message || "获取二维码失败";
        toast(result.message || "获取二维码失败", "error");
        return;
      }
      qrSid = result.sid;
      qrImg.src = result.image;
      qrImg.hidden = false;
      qrPlaceholder.hidden = true;
      qrBox.classList.remove("empty");
      qrStatus.textContent = result.message || "请使用智慧树 App 扫码";
      qrTimer = setInterval(pollQr, 1500);
    } catch (e) {
      qrStatus.textContent = e.message || "获取二维码失败";
    } finally {
      qrStart.disabled = false;
    }
  }

  async function pollQr() {
    if (!qrSid) return;
    const result = await api("/api/login/qr/poll?sid=" + encodeURIComponent(qrSid));
    if (!result.ok) {
      qrStatus.textContent = result.message || "二维码状态查询失败";
      stopQrPolling();
      return;
    }
    qrStatus.textContent = result.message || "";
    if (result.logged_in) {
      stopQrPolling();
      toast("扫码登录成功", "success");
      coursesLoaded = false;
      qrImg.hidden = true;
      qrPlaceholder.hidden = false;
      qrBox.classList.add("empty");
      await refreshStatus(true);
    } else if (result.status === 2 || result.status === 3) {
      stopQrPolling();
    }
  }

  qrStart.addEventListener("click", startQrLogin);

  // ------------------------------------------------------------------
  // Cookie 导入
  // ------------------------------------------------------------------
  cookieImport.addEventListener("click", async () => {
    const raw = cookieInput.value.trim();
    if (!raw) {
      toast("请先粘贴 Cookie", "error");
      return;
    }
    cookieImport.disabled = true;
    try {
      const result = await api("/api/login/cookies", {
        method: "POST",
        body: { cookies: raw },
      });
      if (result.ok) {
        toast("Cookie 导入成功", "success");
        cookieInput.value = "";
        coursesLoaded = false;
        await refreshStatus(true);
      } else {
        toast(result.message || "Cookie 导入失败", "error");
      }
    } finally {
      cookieImport.disabled = false;
    }
  });

  // ------------------------------------------------------------------
  // 状态
  // ------------------------------------------------------------------
  function applyConfig(config) {
    if (configApplied || !config) return;
    configApplied = true;
    if (config.username) accountInput.value = config.username;
    speedInput.value = config.speed ?? "";
    thresholdInput.value = config.threshold ?? "";
    limitInput.value = config.limit ?? 0;
    treeViewInput.checked = config.tree_view !== false;
    progressbarInput.checked = config.progressbar_view === true;
  }

  async function refreshStatus(forceCourses = false) {
    const result = await api("/api/status");
    if (!result.ok) return;

    const wasLoggedIn = loggedIn;
    loggedIn = !!result.logged_in;
    applyConfig(result.config);

    if (loggedIn) {
      loginView.hidden = true;
      dashboard.hidden = false;
      logoutBtn.hidden = false;
      if (!wasLoggedIn) {
        setBadge("已登录", "badge-on");
        lastLogId = 0;
      }
      setTaskBadge(!!result.task_running, !!result.stop_requested);
      setBadge(result.task_running ? "任务运行中" : "已登录",
        result.task_running ? "badge-busy" : "badge-on");
      if (!coursesLoaded || forceCourses) {
        coursesLoaded = true;
        loadCourses();
      }
    } else {
      loginView.hidden = false;
      dashboard.hidden = true;
      logoutBtn.hidden = true;
      setBadge("未登录", "badge-off");
      setTaskBadge(false, false);
      resetProgress();
    }
  }

  // ------------------------------------------------------------------
  // 课程
  // ------------------------------------------------------------------
  function updateCourseCount() {
    const all = courseList.querySelectorAll(".course-check");
    const checked = courseList.querySelectorAll(".course-check:checked");
    courseCount.textContent = `${checked.length} / ${all.length} 门课程`;
    selectAll.checked = all.length > 0 && checked.length === all.length;
    selectAll.indeterminate = checked.length > 0 && checked.length < all.length;
  }

  function renderCourses(courses) {
    courseList.innerHTML = "";
    if (!courses.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "没有获取到课程";
      courseList.appendChild(empty);
      updateCourseCount();
      return;
    }
    courses.forEach((course) => {
      const item = document.createElement("label");
      item.className = "course-item";

      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "course-check";
      check.dataset.id = course.id;
      check.dataset.type = course.type;

      const meta = document.createElement("div");
      meta.className = "course-meta";
      const name = document.createElement("div");
      name.className = "course-name";
      name.appendChild(escapeText(course.name || "(未命名课程)"));
      const id = document.createElement("div");
      id.className = "course-id";
      id.appendChild(escapeText(course.id));
      meta.appendChild(name);
      meta.appendChild(id);

      const type = document.createElement("span");
      type.className = "course-type " + (course.type || "");
      type.textContent = course.type === "zhidao" ? "知到共享课" : "校内学分课";

      item.appendChild(check);
      item.appendChild(meta);
      item.appendChild(type);
      courseList.appendChild(item);
    });
    updateCourseCount();
  }

  async function loadCourses() {
    refreshCoursesBtn.disabled = true;
    refreshCoursesBtn.textContent = "刷新中...";
    try {
      const result = await api("/api/courses");
      if (!result.ok) {
        toast(result.message || "获取课程失败", "error");
        if (result.message && result.message.includes("登录")) {
          await refreshStatus();
        }
        return;
      }
      renderCourses(result.courses || []);
      if (!result.courses || !result.courses.length) {
        toast("未获取到课程，可能课程列表为空", "");
      }
    } finally {
      refreshCoursesBtn.disabled = false;
      refreshCoursesBtn.textContent = "刷新";
    }
  }

  refreshCoursesBtn.addEventListener("click", loadCourses);
  selectAll.addEventListener("change", () => {
    courseList.querySelectorAll(".course-check").forEach((check) => {
      check.checked = selectAll.checked;
    });
    updateCourseCount();
  });
  courseList.addEventListener("change", (event) => {
    if (event.target.classList.contains("course-check")) updateCourseCount();
  });

  // ------------------------------------------------------------------
  // 任务
  // ------------------------------------------------------------------
  function parseVideos() {
    const raw = videosInput.value.trim();
    if (!raw) return [];
    return raw.split(/[,\n;]/)
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => part.replace(/\s+/, ":"));
  }

  async function startTask(all) {
    if (!loggedIn) {
      toast("请先登录", "error");
      return;
    }
    const selected = Array.from(courseList.querySelectorAll(".course-check:checked"))
      .map((check) => check.dataset.id);
    const videos = parseVideos();
    if (!all && selected.length === 0 && videos.length === 0) {
      toast("请选择课程或填写视频", "error");
      return;
    }
    const result = await api("/api/task/start", {
      method: "POST",
      body: {
        all,
        courses: all ? [] : selected,
        videos,
        speed: speedInput.value === "" ? null : Number(speedInput.value),
        threshold: thresholdInput.value === "" ? null : Number(thresholdInput.value),
        limit: Number(limitInput.value || 0),
        tree_view: treeViewInput.checked,
        progressbar_view: progressbarInput.checked,
      },
    });
    if (result.ok) {
      toast(all ? "已开始刷全部课程" : "任务已启动", "success");
      lastLogId = 0;
      logEl.textContent = "";
      await refreshStatus();
    } else {
      toast(result.message || "启动任务失败", "error");
    }
  }

  startSelected.addEventListener("click", () => startTask(false));
  startAll.addEventListener("click", () => startTask(true));
  stopTask.addEventListener("click", async () => {
    const result = await api("/api/task/stop", { method: "POST" });
    toast(result.message || (result.ok ? "已发送停止请求" : "停止失败"),
      result.ok ? "" : "error");
    if (result.ok) await refreshStatus();
  });

  // ------------------------------------------------------------------
  // 日志
  // ------------------------------------------------------------------
  function appendLog(item) {
    const nearBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 40;
    const line = document.createElement("div");
    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = `[${item.time}] `;
    const text = document.createElement("span");
    const level = (item.level || "").toUpperCase();
    if (level === "ERROR" || level === "CRITICAL") text.className = "log-error";
    else if (level === "WARNING") text.className = "log-warning";
    else if (level === "DEBUG") text.className = "log-debug";
    text.textContent = item.text;
    line.appendChild(time);
    line.appendChild(text);
    logEl.appendChild(line);
    if (nearBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  async function pollLogs() {
    const result = await api("/api/logs?since=" + lastLogId);
    if (!result.ok || !result.logs) return;
    result.logs.forEach((item) => {
      appendLog(item);
      lastLogId = Math.max(lastLogId, item.id);
    });
  }

  clearLogBtn.addEventListener("click", () => { logEl.textContent = ""; });

  // ------------------------------------------------------------------
  // 任务进度
  // ------------------------------------------------------------------
  function formatTime(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) {
      return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function resetProgress() {
    if (progressBar) progressBar.style.width = "0%";
    if (progressPercent) progressPercent.textContent = "0%";
    if (progressCourse) progressCourse.textContent = "等待任务";
    if (progressVideo) progressVideo.textContent = "";
    if (progressDetail) progressDetail.textContent = "";
  }

  function renderProgress(progress) {
    if (!progress) return;
    const overall = Math.max(0, Math.min(100, Number(progress.overall_percent) || 0));
    if (progressBar) progressBar.style.width = overall.toFixed(1) + "%";
    if (progressPercent) progressPercent.textContent = overall.toFixed(1) + "%";

    if (progressCourse) {
      if (progress.running) {
        if (progress.course_total) {
          progressCourse.textContent =
            `课程 ${progress.course_index}/${progress.course_total} · ${progress.course_name || progress.course_id || ""}`;
        } else {
          progressCourse.textContent = progress.message || "任务运行中";
        }
      } else if (progress.started_at) {
        progressCourse.textContent = progress.message || "任务结束";
      } else {
        progressCourse.textContent = "等待任务";
      }
    }

    if (progressVideo) {
      progressVideo.textContent = progress.video_total
        ? `视频 ${progress.video_index}/${progress.video_total}`
        : "";
    }

    if (progressDetail) {
      if (progress.video_id) {
        const name = progress.video_name || progress.video_id;
        progressDetail.textContent =
          `${name} · ${formatTime(progress.played)} / ${formatTime(progress.duration)}`;
      } else if (progress.video_total) {
        progressDetail.textContent = "等待视频";
      } else {
        progressDetail.textContent = "";
      }
    }
  }

  async function pollProgress() {
    const result = await api("/api/task/progress");
    if (result.ok) renderProgress(result.progress);
  }

  // ------------------------------------------------------------------
  // 退出
  // ------------------------------------------------------------------
  logoutBtn.addEventListener("click", async () => {
    const result = await api("/api/logout", { method: "POST" });
    if (result.ok) {
      loggedIn = false;
      coursesLoaded = false;
      courseList.innerHTML = "";
      stopQrPolling();
      toast("已退出登录", "");
      await refreshStatus();
    }
  });

  // ------------------------------------------------------------------
  // 初始化
  // ------------------------------------------------------------------
  setInterval(refreshStatus, 3000);
  setInterval(pollLogs, 1000);
  setInterval(pollProgress, 700);
  refreshStatus();
  pollLogs();
  pollProgress();
})();
