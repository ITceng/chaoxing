/* global fetch, document, window */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = {
    csrf: "",
    accounts: [],
    meta: { providers: [] },
    settings: null,
    logs: { id: null, after: 0, timer: null, node: null },
    polling: false,
    busy: false,
  };

  const STATUS = {
    idle: ["空闲", ""],
    starting: ["启动中", "queued"],
    queued: ["排队中", "queued"],
    waiting: ["等待资源", "queued"],
    running: ["运行中", "running"],
    paused: ["已暂停", "paused"],
    stopping: ["停止中", "stopping"],
    completed: ["已完成", "completed"],
    failed: ["失败", "failed"],
    stopped: ["已停止", "failed"],
    cancelled: ["已取消", "failed"],
  };

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    const options = attrs || {};
    Object.keys(options).forEach((key) => {
      const value = options[key];
      if (value === undefined || value === null || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "value") node.value = value;
      else if (key === "checked") node.checked = Boolean(value);
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else {
        node.setAttribute(key, String(value));
      }
    });
    children.forEach((child) => {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child)) : child);
    });
    return node;
  }

  function toast(message, kind) {
    const node = $("#toast");
    node.textContent = String(message);
    let extra = "";
    if (kind === true || kind === "error") extra = " error";
    else if (kind === "ok") extra = " ok";
    node.className = "toast" + extra;
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(() => node.classList.add("hidden"), 3200);
  }

  async function testConnection(ev, payload) {
    const btn = ev.currentTarget;
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = "测试中…";
    try {
      const res = await api("/api/settings/test-connection", {
        method: "POST",
        body: payload,
      });
      toast(res.msg || (res.ok ? "连接成功" : "连接失败"), res.ok ? "ok" : "error");
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  }

  async function api(path, options) {
    const opts = Object.assign({ credentials: "same-origin" }, options || {});
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    opts.headers = headers;
    const response = await fetch(path, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { data = null; }
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      showAuth();
      throw new Error((data && data.error) || "登录已失效");
    }
    if (!response.ok) {
      throw new Error((data && data.error) || ("HTTP " + response.status));
    }
    return data || {};
  }

  function authView(mode) {
    $("#boot").classList.add("hidden");
    $("#app-view").classList.add("hidden");
    $("#auth-view").classList.remove("hidden");
    $("#setup-form").classList.toggle("hidden", mode !== "setup");
    $("#login-form").classList.toggle("hidden", mode !== "login");
    document.querySelectorAll("[data-error]").forEach((node) => { node.textContent = ""; });
    const focus = mode === "setup"
      ? document.querySelector("#setup-form input[name=username]")
      : document.querySelector("#login-form input[name=username]");
    if (focus) focus.focus();
  }

  function showAuth() {
    authView("login");
    api("/api/auth/status").then((status) => {
      if (!status.configured) authView("setup");
    }).catch(() => {});
  }

  function appView() {
    $("#boot").classList.add("hidden");
    $("#auth-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
  }

  async function init() {
    try {
      const status = await api("/api/auth/status");
      if (!status.configured) {
        authView("setup");
        return;
      }
      if (!status.authenticated) {
        authView("login");
        return;
      }
      const me = await api("/api/auth/me");
      state.csrf = me.csrf;
      $("#user-label").textContent = "· " + (me.user || "");
      appView();
      await loadMeta();
      await loadAccounts();
      window.setInterval(pollAccounts, 3000);
    } catch (e) {
      $("#boot").classList.add("hidden");
      authView("login");
      toast(e.message, true);
    }
  }

  async function loadMeta() {
    try {
      state.meta = await api("/api/meta");
    } catch (e) {
      state.meta = { providers: [] };
    }
  }

  function statusOf(account) {
    return (account.runtime && account.runtime.status) || "idle";
  }

  function badge(account) {
    const status = statusOf(account);
    const item = STATUS[status] || [status, ""];
    return h("span", { class: "badge " + item[1], text: item[0] });
  }

  function fmtTime(seconds) {
    if (!seconds) return "-";
    const date = new Date(seconds * 1000);
    return date.toLocaleString();
  }

  function fmtDuration(account) {
    const runtime = account.runtime || {};
    if (!runtime.started_at) return "-";
    const end = runtime.finished_at || (Date.now() / 1000);
    const seconds = Math.max(0, Math.round(end - runtime.started_at));
    if (seconds < 60) return seconds + " 秒";
    if (seconds < 3600) return Math.round(seconds / 60) + " 分钟";
    return (seconds / 3600).toFixed(1) + " 小时";
  }

  function platformName(key) {
    if (!key) return "";
    const list = (state.meta && state.meta.platforms) || [];
    const found = list.find((item) => item.key === key);
    if (found) return found.name;
    return key === "chaoxing" ? "超星" : key;
  }

  function restartLine(account) {
    const runtime = account.runtime || {};
    if (runtime.restart_blocked) {
      return h("div", { class: "error-line",
        text: "自动重启已达上限（" + (runtime.restart_count || 0) + " 次/小时），请检查日志后手动启动" });
    }
    if (runtime.restart_at && runtime.restart_at * 1000 > Date.now()) {
      const seconds = Math.max(1, Math.round(runtime.restart_at - Date.now() / 1000));
      return h("div", { class: "muted", style: "font-size:12px",
        text: "失败后自动重启：约 " + seconds + " 秒后重试（第 " + (runtime.restart_count || 0) + " 次）" });
    }
    if (runtime.started_by === "auto" && statusOf(account) === "running") {
      return h("div", { class: "muted", style: "font-size:12px", text: "由无人值守自动启动" });
    }
    return null;
  }

  function card(account) {
    const runtime = account.runtime || {};
    const status = statusOf(account);
    const counts = runtime.counts || {};
    const total = runtime.total || 0;
    const done = runtime.done || 0;
    const progress = Math.max(0, Math.min(100, Number(runtime.progress || 0)));

    const head = h("div", { class: "card-head" },
      h("div", {},
        h("div", { class: "card-title", text: account.name || account.username_hint || account.id }),
        h("div", { class: "card-sub",
          text: [platformName(account.platform), account.username_hint,
            account.use_cookies ? "Cookie 登录" : "账号密码登录",
            account.autostart ? "无人值守" : "手动启动"]
            .filter(Boolean).join(" · ") || account.id }),
      ),
      badge(account),
    );

    const meta = h("div", { class: "meta-row" },
      h("span", { text: "进度 " + done + "/" + total }),
      h("span", { text: "成功 " + (counts.success || 0) }),
      h("span", { text: "失败 " + ((counts.failed || 0) + (counts.cancelled || 0)) }),
      h("span", { text: "跳过 " + ((counts.skipped || 0) + (counts.not_open || 0)) }),
      h("span", { text: "耗时 " + fmtDuration(account) }),
    );

    const bar = h("div", { class: "progress" }, h("div", { style: "width:" + progress + "%" }));
    const current = (runtime.current || []).map((item) =>
      (item.course ? item.course + " / " : "") + (item.chapter || "")).join("；");
    const body = h("div", { class: "card-body" },
      meta,
      bar,
      h("div", { class: "current", text: current ? "正在处理：" + current : "" }),
      runtime.error ? h("div", { class: "error-line", text: "错误：" + runtime.error }) : null,
      restartLine(account),
      h("div", { class: "muted", style: "font-size:12px",
        text: "课程：" + (account.course_list || "全部") +
          " · 倍速 " + account.speed + " · 并发 " + account.jobs +
          (account.provider ? " · 题库 " + account.provider : "") }),
      h("div", { class: "muted", style: "font-size:12px",
        text: runtime.finished_at ? ("结束于 " + fmtTime(runtime.finished_at)) : "" }),
    );

    const actions = h("div", { class: "card-actions" });
    const add = (label, cls, onClick, disabled) => {
      actions.appendChild(h("button", {
        class: "small" + (cls ? " " + cls : ""), text: label,
        onclick: onClick, disabled: disabled || false,
      }));
    };

    if (status === "running" || status === "queued" || status === "waiting" || status === "starting") {
      add("暂停", "", () => runAction(account.id, "pause"));
      add("停止", "danger-ghost", () => runAction(account.id, "stop"));
    } else if (status === "paused") {
      add("继续", "primary", () => runAction(account.id, "resume"));
      add("停止", "danger-ghost", () => runAction(account.id, "stop"));
    } else if (status === "stopping") {
      add("停止中", "", null, true);
    } else {
      add("启动", "primary", () => runAction(account.id, "start"));
    }
    add("日志", "", () => openLogs(account));
    add("编辑", "", () => openAccountForm(account));
    add("删除", "danger-ghost", () => deleteAccount(account));
    body.appendChild(actions);

    return h("article", { class: "card" }, head, body);
  }

  function render() {
    const wrap = $("#accounts");
    wrap.replaceChildren();
    $("#empty").classList.toggle("hidden", state.accounts.length > 0);
    state.accounts.forEach((account) => wrap.appendChild(card(account)));

    const running = state.accounts.filter((a) => ["running", "starting", "queued", "waiting", "stopping"]
      .includes(statusOf(a))).length;
    const paused = state.accounts.filter((a) => statusOf(a) === "paused").length;
    const failed = state.accounts.filter((a) => ["failed", "stopped", "cancelled"].includes(statusOf(a))).length;
    $("#stat-total").textContent = String(state.accounts.length);
    $("#stat-running").textContent = String(running);
    $("#stat-paused").textContent = String(paused);
    $("#stat-failed").textContent = String(failed);
  }

  async function loadAccounts() {
    const data = await api("/api/accounts");
    state.accounts = data.accounts || [];
    render();
  }

  async function pollAccounts() {
    if (state.polling || document.hidden) return;
    state.polling = true;
    try {
      await loadAccounts();
    } catch (e) {
      if (!/登录/.test(e.message)) toast(e.message, true);
    } finally {
      state.polling = false;
    }
  }

  async function runAction(id, action) {
    if (state.busy) return;
    state.busy = true;
    try {
      const data = await api("/api/accounts/" + encodeURIComponent(id) + "/" + action, { method: "POST" });
      toast(data.message || "操作成功");
      await loadAccounts();
    } catch (e) {
      toast(e.message, true);
    } finally {
      state.busy = false;
    }
  }

  async function deleteAccount(account) {
    if (!window.confirm("确定删除账号 “" + (account.name || account.id) + "”？该操作不可恢复。")) return;
    try {
      await api("/api/accounts/" + encodeURIComponent(account.id), { method: "DELETE" });
      toast("账号已删除");
      await loadAccounts();
    } catch (e) {
      toast(e.message, true);
    }
  }

  // ------------------------------------------------------------------ modals
  function closeModal() {
    const root = $("#modal-root");
    root.replaceChildren();
    if (state.logs.timer) {
      window.clearInterval(state.logs.timer);
      state.logs = { id: null, after: 0, timer: null, node: null };
    }
  }

  function modal(title, body, foot, wide) {
    closeModal();
    const backdrop = h("div", {
      class: "modal-backdrop",
      onclick: (event) => { if (event.target === backdrop) closeModal(); },
    },
      h("div", { class: "modal" + (wide ? " wide" : "") },
        h("div", { class: "modal-head" },
          h("h2", { text: title }),
          h("button", { class: "small", text: "关闭", onclick: closeModal }),
        ),
        h("div", { class: "modal-body" }, body),
        foot ? h("div", { class: "modal-foot" }, foot) : null,
      ),
    );
    $("#modal-root").replaceChildren(backdrop);
    return backdrop;
  }

  function field(label, input) {
    return h("label", {}, label, input);
  }

  function option(value, text) {
    return h("option", { value: value, text: text });
  }

  async function openLogs(account) {
    const pre = h("div", { class: "logs", text: "" });
    const foot = h("div", {});
    const clear = h("button", {
      class: "small danger-ghost", text: "清空日志",
      onclick: async () => {
        try {
          await api("/api/accounts/" + encodeURIComponent(account.id) + "/logs", { method: "DELETE" });
          pre.replaceChildren();
          state.logs.after = 0;
          toast("日志已清空");
        } catch (e) { toast(e.message, true); }
      },
    });
    foot.appendChild(clear);
    modal("日志 · " + (account.name || account.id), pre, foot, true);
    state.logs = { id: account.id, after: 0, timer: null, node: pre };
    await loadLogs();
    state.logs.timer = window.setInterval(loadLogs, 2000);
  }

  async function loadLogs() {
    const logs = state.logs;
    if (!logs.id || !logs.node) return;
    try {
      const data = await api("/api/accounts/" + encodeURIComponent(logs.id) +
        "/logs?after=" + logs.after + "&limit=300");
      const items = data.logs || [];
      const node = logs.node;
      const nearBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 40;
      items.forEach((item) => {
        const level = String(item.level || "INFO").toLowerCase();
        node.appendChild(h("div", { class: "l-" + level, text: item.time + "  " + item.message }));
      });
      if (items.length) logs.after = data.next || logs.after;
      while (node.childElementCount > 1500) node.removeChild(node.firstElementChild);
      if (nearBottom) node.scrollTop = node.scrollHeight;
    } catch (e) {
      // 轮询失败不打断界面
    }
  }

  function accountForm(account) {
    const editing = Boolean(account && account.id);
    const name = h("input", { name: "name", maxlength: "64", value: account ? account.name : "" });
    const username = h("input", {
      name: "username", autocomplete: "username", maxlength: "64",
      placeholder: editing && account.username_hint ? "留空保持 " + account.username_hint : "手机号 / 账号",
    });
    const password = h("input", {
      name: "password", type: "password", autocomplete: "new-password",
      placeholder: editing ? "留空表示不修改" : "登录密码",
    });
    const useCookies = h("input", { type: "checkbox", name: "use_cookies", checked: account ? account.use_cookies : false });
    const cookies = h("textarea", {
      name: "cookies_text",
      placeholder: editing ? "留空表示保持原 Cookie（填写则覆盖）" : "可选：直接粘贴 Cookie 字符串",
    });
    const courseList = h("input", {
      name: "course_list", maxlength: "2000", value: account ? account.course_list : "",
      placeholder: "课程 ID，逗号分隔；留空或 * 表示全部课程",
    });
    const speed = h("input", { name: "speed", type: "number", min: "1", max: "2", step: "0.1",
      value: account ? account.speed : 1 });
    const jobs = h("input", { name: "jobs", type: "number", min: "1", max: "8",
      value: account ? account.jobs : 2 });
    const notOpen = h("select", { name: "notopen_action" },
      option("retry", "retry（重试）"),
      option("ask", "ask（跳过并记录）"),
      option("continue", "continue（直接跳过）"),
    );
    if (account) notOpen.value = account.notopen_action || "retry";
    const courseSerial = h("input", { type: "checkbox", name: "course_serial",
      checked: account ? account.course_serial : true });
    const redo = h("input", { type: "checkbox", name: "work_redo_enabled",
      checked: account ? account.work_redo_enabled : false });
    const maxRetries = h("input", { name: "work_max_retries", type: "number", min: "1", max: "10",
      value: account ? account.work_max_retries : 3 });
    const addCount = h("input", { type: "checkbox", name: "add_learning_count",
      checked: account ? account.add_learning_count : false });
    const target = h("input", { name: "target_count", type: "number", min: "1", max: "10000",
      value: account ? account.target_count : 100 });
    const provider = h("select", { name: "provider" }, option("", "跟随全局设置"));
    (state.meta.providers || []).forEach((item) => provider.appendChild(option(item, item)));
    if (account) provider.value = account.provider || "";
    const notes = h("textarea", { name: "notes", maxlength: "1000", value: account ? account.notes : "" });
    const platform = h("select", { name: "platform" });
    const platforms = (state.meta && state.meta.platforms) || [];
    platforms.forEach((item) => {
      platform.appendChild(option(item.key, item.name + (item.status === "demo" ? "" : "")));
    });
    if (!platforms.length) platform.appendChild(option("chaoxing", "超星学习通 / 泛雅"));
    const currentPlatform = account ? (account.platform || "chaoxing") : (state.meta && state.meta.default_platform) || "chaoxing";
    platform.value = currentPlatform;
    if (!platform.value) platform.selectedIndex = 0;
    const platformHint = h("p", { class: "hint", text: "" });
    const refreshPlatformHint = () => {
      const picked = platforms.find((item) => item.key === platform.value);
      platformHint.textContent = picked ? (picked.summary || "") + (picked.docs ? " · " + picked.docs : "") : "";
    };
    platform.addEventListener("change", refreshPlatformHint);
    refreshPlatformHint();
    const autostart = h("input", { type: "checkbox", name: "autostart",
      checked: account ? account.autostart !== false : true });
    const autoRestart = h("input", { type: "checkbox", name: "auto_restart",
      checked: account ? account.auto_restart !== false : true });

    const form = h("form", { class: "form" },
      h("div", { class: "section-title", text: "平台" }),
      field("网课平台", platform),
      platformHint,

      h("div", { class: "section-title", text: "登录凭据" }),
      h("div", { class: "grid2" },
        field("账号名称", name),
        field("用户名", username),
      ),
      h("div", { class: "grid2" },
        field("密码", password),
        h("label", { class: "check" }, useCookies, "使用 Cookie 登录"),
      ),
      field("Cookie（可选）", cookies),
      h("p", { class: "hint", text: "密码与 Cookie 加密存储；账号之间 Cookie 文件互相隔离。" }),

      h("div", { class: "section-title", text: "学习参数" }),
      field("课程列表", courseList),
      h("div", { class: "grid3" },
        field("倍速（1-2）", speed),
        field("并发章节（1-8）", jobs),
        field("未开放章节", notOpen),
      ),
      h("div", { class: "grid3" },
        h("label", { class: "check" }, courseSerial, "课程内章节串行（推荐）"),
        h("label", { class: "check" }, redo, "允许章节检测重做"),
        field("最大重做次数", maxRetries),
      ),
      h("div", { class: "grid3" },
        h("label", { class: "check" }, addCount, "增加章节学习次数"),
        field("目标次数", target),
        field("题库覆盖", provider),
      ),

      h("div", { class: "section-title", text: "无人值守（服务器自动运行）" }),
      h("div", { class: "grid2" },
        h("label", { class: "check" }, autostart, "面板启动后自动开始（autostart）"),
        h("label", { class: "check" }, autoRestart, "失败后自动重启（auto_restart）"),
      ),
      h("p", { class: "hint", text: "手动停止的账号不会被自动重启；自动重启默认最多 5 次/小时，之后停在“失败”等你处理。" }),
      field("备注", notes),
      h("p", { class: "error", "data-form-error": "" }),
    );

    const submit = h("button", { class: "primary", form: "account-form", type: "submit",
      text: editing ? "保存" : "创建账号" });
    form.id = "account-form";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorNode = form.querySelector("[data-form-error]");
      errorNode.textContent = "";
      const payload = {
        name: name.value.trim(),
        use_cookies: useCookies.checked,
        course_list: courseList.value.trim(),
        speed: Number(speed.value),
        jobs: Number(jobs.value),
        notopen_action: notOpen.value,
        course_serial: courseSerial.checked,
        work_redo_enabled: redo.checked,
        work_max_retries: Number(maxRetries.value),
        add_learning_count: addCount.checked,
        target_count: Number(target.value),
        provider: provider.value,
        platform: platform.value,
        notes: notes.value,
        autostart: autostart.checked,
        auto_restart: autoRestart.checked,
      };
      if (username.value.trim()) payload.username = username.value.trim();
      if (password.value) payload.password = password.value;
      if (cookies.value.trim()) payload.cookies_text = cookies.value.trim();
      try {
        submit.disabled = true;
        if (editing) {
          const data = await api("/api/accounts/" + encodeURIComponent(account.id), { method: "PATCH", body: payload });
          toast(data.note || "已保存");
        } else {
          await api("/api/accounts", { method: "POST", body: payload });
          toast("账号已创建");
        }
        closeModal();
        await loadAccounts();
      } catch (e) {
        errorNode.textContent = e.message;
      } finally {
        submit.disabled = false;
      }
    });

    modal(editing ? "编辑账号" : "添加账号", form, submit, true);
  }

  function openAccountForm(account) { accountForm(account || null); }

  // ----------------------------------------------------------------- settings
  function secretInput(value, placeholder) {
    return h("input", {
      type: "password", autocomplete: "new-password",
      value: "",
      placeholder: value && value !== "***" ? String(value) : (value === "***" ? "已设置，留空保持不变" : (placeholder || "")),
    });
  }

  async function openSettings() {
    let data;
    try {
      data = await api("/api/settings");
    } catch (e) {
      toast(e.message, true);
      return;
    }
    const s = data.settings || {};
    const tiku = s.tiku || {};
    const notification = s.notification || {};

    const maxConcurrent = h("input", { type: "number", min: "1", max: "50",
      value: s.max_concurrent_accounts || 2 });
    const provider = h("select", {}, option("", "不启用题库"));
    (state.meta.providers || []).forEach((item) => provider.appendChild(option(item, item)));
    provider.value = tiku.provider || "";
    const submitMode = h("select", {},
      option("false", "答完保存，不提交"),
      option("true", "覆盖率达标后提交"),
    );
    submitMode.value = String(tiku.submit || "false");
    const coverRate = h("input", { type: "number", min: "0", max: "1", step: "0.01",
      value: tiku.cover_rate !== undefined ? tiku.cover_rate : 0.9 });
    const delay = h("input", { type: "number", min: "0", step: "0.1",
      value: tiku.delay !== undefined ? tiku.delay : 1 });
    const tokens = secretInput(tiku.tokens, "题库 token，多个用英文逗号分隔");
    const tikuUrl = h("input", { value: tiku.url || "", placeholder: "TikuAdapter 地址（可选）" });
    const endpoint = h("input", { value: tiku.endpoint || "", placeholder: "https://api.openai.com/v1" });
    const key = secretInput(tiku.key, "AI API Key");
    const model = h("input", { value: tiku.model || "", placeholder: "模型名称" });
    const interval = h("input", { type: "number", min: "0", step: "0.1",
      value: tiku.min_interval_seconds !== undefined ? tiku.min_interval_seconds : 3 });
    const proxy = h("input", { value: tiku.http_proxy || "", placeholder: "http://proxy:port（可选）" });
    const checkLLM = h("input", { type: "checkbox", checked: String(tiku.check_llm_connection) !== "false" });
    const siliconKey = secretInput(tiku.siliconflow_key, "硅基流动 API Key");
    const siliconModel = h("input", { value: tiku.siliconflow_model || "" });
    const siliconEndpoint = h("input", { value: tiku.siliconflow_endpoint || "" });
    const goAuth = secretInput(tiku.go_authorization, "GO 题 Authorization（可选）");

    const notifyProvider = h("select", {},
      option("", "不启用"),
      option("ServerChan", "ServerChan"),
      option("Qmsg", "Qmsg"),
      option("Bark", "Bark"),
      option("Telegram", "Telegram"),
    );
    notifyProvider.value = notification.provider || "";
    const notifyUrl = secretInput(notification.url, "通知 URL（含 key）");
    const tgChat = h("input", { value: notification.tg_chat_id || "" });

    const form = h("form", { class: "form" },
      h("div", { class: "section-title", text: "运行" }),
      h("div", { class: "grid2" },
        field("同时运行的账号数", maxConcurrent),
        h("p", { class: "hint", text: "超出数量的账号会排队，停止一个账号不会影响其他账号。" }),
      ),

      h("div", { class: "section-title", text: "题库" }),
      h("div", { class: "grid3" },
        field("Provider", provider),
        field("提交模式", submitMode),
        field("最低覆盖率", coverRate),
      ),
      h("div", { class: "grid3" },
        field("查询间隔（秒）", delay),
        field("题库 URL", tikuUrl),
        h("label", { class: "check" }, checkLLM, "启动时检查大模型连接"),
      ),
      field("题库 Tokens", tokens),
      h("p", { class: "hint", text: "敏感字段加密保存；留空表示保持原值。" }),

      h("div", { class: "section-title", text: "AI / 硅基流动" }),
      h("div", { class: "grid2" },
        field("Endpoint", endpoint),
        field("Key", key),
        field("Model", model),
        field("请求间隔（秒）", interval),
        field("HTTP 代理", proxy),
        field("GO题 Authorization", goAuth),
        h("div", { style: "display:flex;gap:8px;align-items:flex-end" },
          h("button", { type: "button", class: "small", text: "测试 AI 连接",
            onclick: (ev) => testConnection(ev, {
              provider: "AI",
              endpoint: endpoint.value.trim(),
              key: key.value,
              model: model.value.trim(),
              http_proxy: proxy.value.trim(),
              min_interval_seconds: Number(interval.value) || 3,
            }) }),
        ),
      ),
      h("div", { class: "grid3" },
        field("硅基流动 Key", siliconKey),
        field("硅基流动模型", siliconModel),
        field("硅基流动 Endpoint", siliconEndpoint),
        h("div", {},
          h("button", { type: "button", class: "small", text: "测试硅基流动连接",
            onclick: (ev) => testConnection(ev, {
              provider: "SiliconFlow",
              siliconflow_endpoint: siliconEndpoint.value.trim(),
              siliconflow_key: siliconKey.value,
              siliconflow_model: siliconModel.value.trim(),
              min_interval_seconds: Number(interval.value) || 3,
            }) }),
        ),
      ),

      h("div", { class: "section-title", text: "外部通知" }),
      h("div", { class: "grid3" },
        field("Provider", notifyProvider),
        field("Chat ID（Telegram）", tgChat),
      ),
      field("通知 URL", notifyUrl),
      h("p", { class: "error", "data-form-error": "" }),
    );

    const submit = h("button", { class: "primary", type: "submit", form: "settings-form", text: "保存设置" });
    form.id = "settings-form";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const errorNode = form.querySelector("[data-form-error]");
      errorNode.textContent = "";
      const tikuPatch = {
        provider: provider.value,
        submit: submitMode.value,
        cover_rate: Number(coverRate.value),
        delay: Number(delay.value),
        url: tikuUrl.value.trim(),
        endpoint: endpoint.value.trim(),
        model: model.value.trim(),
        min_interval_seconds: Number(interval.value),
        http_proxy: proxy.value.trim(),
        check_llm_connection: checkLLM.checked ? "true" : "false",
        siliconflow_model: siliconModel.value.trim(),
        siliconflow_endpoint: siliconEndpoint.value.trim(),
      };
      if (tokens.value) tikuPatch.tokens = tokens.value;
      if (key.value) tikuPatch.key = key.value;
      if (siliconKey.value) tikuPatch.siliconflow_key = siliconKey.value;
      if (goAuth.value) tikuPatch.go_authorization = goAuth.value;
      const notifyPatch = {
        provider: notifyProvider.value,
        tg_chat_id: tgChat.value.trim(),
      };
      if (notifyUrl.value) notifyPatch.url = notifyUrl.value;
      try {
        submit.disabled = true;
        await api("/api/settings", {
          method: "PUT",
          body: {
            max_concurrent_accounts: Number(maxConcurrent.value),
            tiku: tikuPatch,
            notification: notifyPatch,
          },
        });
        toast("设置已保存");
        closeModal();
        await loadMeta();
      } catch (e) {
        errorNode.textContent = e.message;
      } finally {
        submit.disabled = false;
      }
    });

    modal("设置", form, submit, true);
  }

  async function openAudit() {
    let data;
    try { data = await api("/api/audit?limit=200"); } catch (e) { toast(e.message, true); return; }
    const rows = (data.logs || []).slice().reverse().map((item) => h("tr", {},
      h("td", { text: fmtTime(item.ts) }),
      h("td", { text: item.actor || "" }),
      h("td", { text: item.ip || "" }),
      h("td", { text: item.action || "" }),
      h("td", { text: item.target || "" }),
      h("td", { text: item.result || "" }),
    ));
    const table = h("table", { class: "audit" },
      h("thead", {}, h("tr", {},
        h("th", { text: "时间" }), h("th", { text: "操作者" }), h("th", { text: "IP" }),
        h("th", { text: "动作" }), h("th", { text: "对象" }), h("th", { text: "结果" }),
      )),
      h("tbody", {}, ...rows),
    );
    modal("审计日志", table, null, true);
  }

  function openPassword() {
    const oldPwd = h("input", { type: "password", autocomplete: "current-password" });
    const newPwd = h("input", { type: "password", autocomplete: "new-password" });
    const confirm = h("input", { type: "password", autocomplete: "new-password" });
    const error = h("p", { class: "error" });
    const form = h("form", { class: "form" },
      field("原密码", oldPwd), field("新密码（至少 10 位）", newPwd), field("确认新密码", confirm), error);
    form.id = "password-form";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.textContent = "";
      if (newPwd.value !== confirm.value) { error.textContent = "两次新密码不一致"; return; }
      try {
        const data = await api("/api/auth/password", {
          method: "POST",
          body: { old_password: oldPwd.value, new_password: newPwd.value },
        });
        state.csrf = data.csrf;
        toast("密码已修改");
        closeModal();
      } catch (e) {
        error.textContent = e.message;
      }
    });
    modal("修改密码", form, h("button", { class: "primary", type: "submit", form: "password-form", text: "保存" }));
  }

  // ------------------------------------------------------------------- events
  document.querySelector("#setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const error = form.querySelector("[data-error]");
    error.textContent = "";
    const data = new FormData(form);
    if (data.get("password") !== data.get("confirm")) {
      error.textContent = "两次密码不一致";
      return;
    }
    try {
      const result = await api("/api/auth/setup", {
        method: "POST",
        body: { username: data.get("username"), password: data.get("password") },
      });
      state.csrf = result.csrf;
      $("#user-label").textContent = "· " + result.user;
      appView();
      await loadMeta();
      await loadAccounts();
    } catch (e) {
      error.textContent = e.message;
    }
  });

  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const error = form.querySelector("[data-error]");
    error.textContent = "";
    const data = new FormData(form);
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: { username: data.get("username"), password: data.get("password") },
      });
      state.csrf = result.csrf;
      $("#user-label").textContent = "· " + result.user;
      appView();
      await loadMeta();
      await loadAccounts();
    } catch (e) {
      error.textContent = e.message;
    }
  });

  $("#btn-add").addEventListener("click", () => openAccountForm(null));
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-audit").addEventListener("click", openAudit);
  $("#btn-password").addEventListener("click", openPassword);

  $("#btn-start-all").addEventListener("click", async () => {
    try {
      const data = await api("/api/actions/start-all", { method: "POST" });
      const failed = (data.results || []).filter((x) => !x.ok);
      toast(failed.length ? ("已启动，失败 " + failed.length + " 个") : "已启动全部账号", failed.length > 0);
      await loadAccounts();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-pause-all").addEventListener("click", async () => {
    try {
      await api("/api/actions/pause-all", { method: "POST" });
      toast("已暂停全部运行中的账号");
      await loadAccounts();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-stop-all").addEventListener("click", async () => {
    if (!window.confirm("确定停止全部正在运行的账号？")) return;
    try {
      await api("/api/actions/stop-all", { method: "POST" });
      toast("已发送停止指令");
      await loadAccounts();
    } catch (e) { toast(e.message, true); }
  });

  $("#btn-logout").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    state.csrf = "";
    showAuth();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  init();
})();
