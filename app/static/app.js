"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const form = $("#profile-form");
  const state = { profile: {}, revision: null, generation: null, loaded: false, baseline: {}, dirty: false, conflict: false, current: null, busy: false, jobs: [], previous: [], filter: "all", sources: [], model: {}, pendingChat: null, chatDraft: null, chatController: null, selectedJobs: new Map(), taskId: null, timer: null, pollToken: 0, taskActive: false, deleting: false };
  state.intakeController = null;
  state.intakePreview = null;
  state.intakeConfirmedPatch = {};
  state.scheduleDraft = null;
  const fieldLabels = { city: "工作城市", cities: "意向城市", accepted_schedules: "可接受的休息安排", priorities: "条件优先级", notes: "补充说明" };
  const taskStorage = "fanwan-radar.active-task.v2";
  let dirtyTimer;
  let sourcesLoaded = false;
  const cardCaches = new WeakMap();
  let selectionSignature = "";
  const arrays = new Set(["cities", "districts", "target_roles", "skills", "employment_types", "industries", "excluded_terms"]);
  const numerics = new Set(["experience_years", "salary_min", "salary_max", "fund_base_min", "max_daily_hours", "max_commute_minutes"]);
  const booleans = new Set(["insurance_during_probation", "accept_night_shift", "accept_rotating_shift", "accept_overtime", "accept_outsourcing", "accept_dispatch", "allow_remote", "allow_relocation"]);
  const verdicts = { match: "较匹配", needs_confirmation: "待确认", mismatch: "不匹配" };
  const periods = { month: "月", year: "年", day: "天", hour: "小时", unknown: "周期待确认" };
  const sourceNames = { boss: "BOSS", zhaopin: "智联招聘", liepin: "猎聘", public: "公开搜索", ncss: "国家大学生就业服务平台", import: "用户提供" };
  const schedules = { weekend_double: "周末双休", weekly_double: "每周休两天", weekly_single: "每周单休", alternating: "大小周", rotation: "轮休", custom_cycle: "自定义上休周期" };
  const statuses = { success: "成功", empty: "采集成功 · 未发现职位", partial: "部分完成", unauthenticated: "需要登录", restricted: "访问受限", capture_timeout: "采集超时", cdp_error: "浏览器连接错误", schema_error: "页面结构无法识别", unavailable: "不可用", unsupported: "暂不支持", cancelled: "已取消", pending: "等待中", queued: "排队中", running: "采集中", completed: "已完成", failed: "失败", cancelling: "正在取消", available: "可用", ready: "连接就绪", connected: "浏览器已连接 · 登录未确认", browser_connected: "浏览器已连接 · 登录未确认", unknown: "尚未确认" };
  const stages = { list: "职位列表", detail: "职位详情", planning: "生成计划", collecting: "采集", finished: "结束", queued: "排队", list_only: "列表信息", full_jd: "完整 JD", connecting: "连接来源", searching: "查询职位", parsing: "解析职位", saving: "保存结果", cancelled: "已取消", starting: "准备采集", browser: "连接浏览器", login: "检查登录", search: "查询职位", done: "完成", error: "来源错误" };
  const list = (v) => Array.isArray(v) ? v : [];
  const text = (v, fallback = "") => typeof v === "string" || typeof v === "number" ? String(v) : fallback;
  const split = (v) => v.split(/[,，、;；\n]+/).map((s) => s.trim()).filter(Boolean);
  const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const node = (tag, cls, value) => { const el = document.createElement(tag); if (cls) el.className = cls; if (value !== undefined) el.textContent = text(value); return el; };
  const timestamp = (v) => { const d = new Date(v); return v && Number.isFinite(d.valueOf()) ? d.toLocaleString("zh-CN") : "时间未提供"; };
  const warnings = (v) => list(v).map((w) => typeof w === "string" ? w : text(w?.message)).filter(Boolean).join("\n");
  const meta = () => ({ expected_revision: state.revision, generation: state.generation });
  const realJobs = (v) => list(v).filter((j) => j && typeof j === "object" && !j.is_demo && j.source !== "demo");
  const labelStatus = (v) => statuses[v] || text(v, "尚未确认");
  const describe = (v) => {
    if (v === null || v === undefined || v === "") return "未设置";
    if (typeof v === "boolean") return v ? "是" : "否";
    if (Array.isArray(v)) return v.length ? v.map(describe).join("；") : "未设置";
    if (typeof v === "object") return Object.entries(v).map(([k, value]) => `${k}: ${describe(value)}`).join(" · ");
    return text(v);
  };

  async function api(path, { method = "GET", body, headers = {} } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const options = { method, signal: controller.signal, headers: { ...headers } };
      if (body instanceof FormData) options.body = body;
      else if (body !== undefined) { options.body = JSON.stringify(body); options.headers["Content-Type"] = "application/json"; }
      const response = await fetch(path, options);
      let data; try { data = await response.json(); } catch { data = {}; }
      if (!response.ok) {
        const detail = text(data.detail, "请求未完成，请检查输入或稍后重试。");
        const error = new Error(detail.length > 1500 || detail.includes("Traceback") ? "服务暂时无法完成请求。" : detail);
        error.status = response.status; error.code = data.code; throw error;
      }
      return data || {};
    } catch (error) {
      if (error.name === "AbortError") throw new Error("等待超时，后台可能仍在处理。请重试读取状态；聊天重试会沿用请求编号。");
      if (error instanceof TypeError) throw new Error("无法连接本地服务，请确认服务可用后重试。");
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function notify(value, type = "success") { const el = $("#global-status"); el.textContent = value; el.className = `global-status ${type}`; el.hidden = !value; }
  function showDialog(id) { const d = $(id); if (!d.open) d.showModal(); }
  function syncControls() {
    const streaming = !!state.chatController;
    document.querySelectorAll("#profile-form input, #profile-form select, #profile-form textarea, #profile-form button, #chat-form textarea, #chat-form button, #chat-suggestions button, #search-form input, #search-form button, #import-form input, #import-form textarea, #import-form button, #confirm-delete, #delete-data, .application-button, .job-select, .job-analyze, .analysis-actions button, #selected-jobs button").forEach((el) => {
      el.disabled = state.busy || !state.loaded || el.dataset.unavailable === "true" || el.dataset.missingId === "true";
    });
    $("#search-button").disabled = state.busy || !state.loaded || state.taskActive || streaming || !sourcesLoaded;
    $("#preview-plan").disabled = state.busy || !state.loaded || state.taskActive || streaming || !sourcesLoaded;
    $("#open-import").disabled = state.busy || !state.loaded || state.taskActive || streaming;
    document.querySelectorAll("#save-profile, #reload-profile, #use-current-profile, #confirm-delete, #delete-data, .application-button").forEach((el) => { el.disabled = state.busy || !state.loaded || streaming || el.dataset.missingId === "true"; });
    $("#profile-stream-help").hidden = !streaming;
    $("#compare-jobs").disabled = state.busy || !state.loaded || state.selectedJobs.size < 2;
    $("#clear-selection").disabled = state.busy || !state.selectedJobs.size;
    $("#stop-chat").hidden = !state.chatController;
    $("#retry-chat").hidden = !state.pendingChat || !!state.chatController;
    $("#discard-chat").hidden = !state.pendingChat || !!state.chatController;
    $("#retry-chat").disabled = state.busy || !state.loaded || streaming;
    $("#discard-chat").disabled = state.busy || streaming;
    $("#send-chat").disabled = state.busy || !state.loaded || !!state.pendingChat;
    $("#chat-input").readOnly = !!state.pendingChat;
    document.querySelectorAll("#chat-suggestions button").forEach((button) => { button.disabled = state.busy || !state.loaded || !!state.pendingChat; });
    $("#chat-form").setAttribute("aria-busy", String(streaming));
    $("#messages").setAttribute("aria-busy", String(streaming));
    $("#interpret-profile").disabled = state.busy || !state.loaded || state.conflict || !!state.intakeController;
    $("#apply-example").disabled = state.busy || !!state.intakeController;
    $("#cancel-interpret").hidden = !state.intakeController;
    $("#profile-intake-form").setAttribute("aria-busy", String(!!state.intakeController));
    $("#apply-intake").disabled = state.busy || !state.loaded || state.conflict || !!state.intakeController || !state.intakePreview || state.intakePreview.stale || !state.intakePreview.rows.some((row) => row.input.checked && !row.input.disabled);
    $("#discard-intake").disabled = state.busy;
  }
  async function operation(button, pending, action, target) {
    if (state.busy) return;
    if (state.chatController) { notify("分析生成中，可以继续编辑条件草稿；请等待完成或停止生成后再保存、搜索或修改数据。", "error"); return; }
    const old = button.textContent; state.busy = true; syncControls(); button.textContent = pending;
    try { await action(); }
    catch (error) {
      if (error.status === 409 && (error.code === "revision_conflict" || /画像|版本|清除|重置/.test(error.message))) markConflict();
      notify(error.message, "error"); if (target) target.textContent = error.message;
    } finally { state.busy = false; button.textContent = old; syncControls(); }
  }

  // Fields are rendered from a local schema; remote strings only enter text/value properties.
  function inputField(name, title, type = "text", options = {}) {
    fieldLabels[name] = title;
    const label = node("label", "", title);
    let input;
    if (Array.isArray(type)) {
      input = node("select"); type.forEach(([value, title]) => input.add(new Option(title, value)));
    } else { input = node("input"); input.type = type; }
    input.name = name;
    for (const [key, value] of Object.entries(options)) input.setAttribute(key, value);
    label.append(input); return label;
  }
  const tri = [["", "未设置"], ["true", "接受 / 是"], ["false", "不接受 / 否"]];
  function group(container, title, priority, fields) {
    const section = node("fieldset", "condition-group"); section.append(node("legend", "", title));
    if (priority) {
      const label = inputField(`priority.${priority}`, `${title}优先级`, [["", "自动 · 填写后为必须"], ["must", "必须"], ["prefer", "偏好"], ["ignore", "忽略"]]);
      label.className = "priority-control"; $("#profile-priorities").append(label);
    }
    fields.forEach((field) => section.append(field)); container.append(section); return section;
  }
  function buildProfile() {
    const common = $("#profile-common"), advanced = $("#profile-advanced"), background = $("#profile-background");
    group(background, "基本信息", null, [inputField("name", "称呼（可选）", "text", { autocomplete: "name", placeholder: "如何称呼你" }), inputField("skills", "技能 · 可多项", "text", { placeholder: "例如：Excel、客户沟通" })]);
    group(background, "经验", "experience", [inputField("experience_years", "工作经验 / 年", "number", { min: "0", max: "70", step: "0.5", placeholder: "未填写，不预设经验" })]);
    group(background, "学历", "education", [inputField("education", "最高学历", [["", "未填写"], ...["初中及以下", "高中", "中专", "大专", "本科", "硕士", "博士", "其他"].map((v) => [v, v])])]);
    group(common, "目标岗位", "role", [inputField("target_roles", "岗位 · 可多项", "text", { placeholder: "填写你希望从事的岗位" })]);
    const location = group(common, "工作地点", "location", [inputField("cities", "意向城市 · 可多项", "text", { placeholder: "例如：郑州、洛阳" })]);
    common.prepend(location);
    group(advanced, "地点细项", null, [inputField("districts", "区县 · 可多项", "text", { placeholder: "例如：郑州金水区" }), inputField("allow_relocation", "是否接受异地搬迁", tri)]);
    const salary = node("div", "field-pair");
    salary.append(inputField("salary_min", "最低薪资 / 元", "number", { min: "0", step: "1", placeholder: "未设置" }), inputField("salary_max", "最高薪资 / 元", "number", { min: "0", step: "1", placeholder: "不限" }));
    group(common, "薪资", "salary", [salary, inputField("salary_period", "薪资周期", [["month", "按月"], ["year", "按年"], ["day", "按天"], ["hour", "按小时"]])]);
    group(advanced, "薪资口径", null, [inputField("salary_basis", "薪资口径", [["unspecified", "未指定"], ["base", "底薪 / 固定薪资"], ["advertised", "招聘标示薪资"]])]);
    group(common, "社保与公积金", "insurance", [inputField("insurance_requirement", "保险要求", [["any", "未限制"], ["five_insurances", "五险"], ["five_and_fund", "五险一金"]])]);
    group(advanced, "缴纳细项", null, [inputField("insurance_during_probation", "试用期是否需要缴纳", [["", "未设置"], ["true", "需要缴纳"], ["false", "允许不缴纳"]]), inputField("fund_base_min", "最低公积金基数 / 元", "number", { min: "0", step: "1", placeholder: "未设置" })]);
    const scheduleGroup = group(common, "可接受的休息安排", "schedule", []);
    const choices = node("div", "schedule-options");
    Object.entries(schedules).forEach(([kind, title]) => {
      const label = node("label", "checkbox-label"); const input = node("input"); input.type = "checkbox"; input.name = `schedule.${kind}`; label.append(input, document.createTextNode(title)); choices.append(label);
    });
    scheduleGroup.append(choices);
    const scheduleDetails = node("details", "schedule-details"); scheduleDetails.append(node("summary", "", "自定义周期与固定休息日")); scheduleGroup.append(scheduleDetails);
    const cycle = node("div", "field-pair"); cycle.append(inputField("cycle.work_days", "每周期上班 / 天", "number", { min: "1", max: "365", step: "1", placeholder: "N" }), inputField("cycle.rest_days", "每周期休息 / 天", "number", { min: "1", max: "365", step: "1", placeholder: "M" })); scheduleDetails.append(cycle);
    const weekdays = node("div", "weekday-options");
    ["一", "二", "三", "四", "五", "六", "日"].forEach((day, i) => { const label = node("label", "checkbox-label"); const input = node("input"); input.type = "checkbox"; input.name = `weekday.${i + 1}`; label.append(input, document.createTextNode(`周${day}`)); weekdays.append(label); });
    scheduleDetails.append(node("p", "field-help", "固定休息日（可选，应用于所选安排；固定周末双休仍为周六、周日）"), weekdays, node("p", "field-help", "多种安排表示任选其一。自定义周期须同时填写上班 N 天、休息 M 天。"));
    group(advanced, "每日工时", "hours", [inputField("max_daily_hours", "每日最多工作 / 小时", "number", { min: "0.5", max: "24", step: "0.5", placeholder: "未设置" })]);
    group(advanced, "夜班", "night_shift", [inputField("accept_night_shift", "是否接受夜班", tri)]);
    group(advanced, "倒班", "rotating_shift", [inputField("accept_rotating_shift", "是否接受倒班", tri)]);
    group(advanced, "加班", "overtime", [inputField("accept_overtime", "是否接受加班", tri)]);
    const hours = node("div", "field-pair"); hours.append(inputField("work_start", "期望上班时间", "time"), inputField("work_end", "期望下班时间", "time")); group(advanced, "工作时段", "work_time", [hours]);
    group(advanced, "用工形式", "employment", [inputField("employment_types", "用工类型 · 可多项", "text", { placeholder: "全职、兼职、实习等" })]);
    group(advanced, "行业", "industry", [inputField("industries", "意向行业 · 可多项")]);
    group(advanced, "排除条件", "excluded_terms", [inputField("excluded_terms", "不考虑的关键词 · 可多项")]);
    group(advanced, "外包", "outsourcing", [inputField("accept_outsourcing", "是否接受外包", tri)]);
    group(advanced, "劳务派遣", "dispatch", [inputField("accept_dispatch", "是否接受派遣", tri)]);
    group(advanced, "远程", "remote", [inputField("allow_remote", "是否接受远程", tri)]);
    group(advanced, "通勤", "commute", [inputField("max_commute_minutes", "最长单程通勤 / 分钟", "number", { min: "0", step: "1", placeholder: "未设置" })]);
  }

  function scheduleSignature() {
    return JSON.stringify(Array.from(form.querySelectorAll("[name^='schedule.'], [name^='cycle.'], [name^='weekday.']"), (input) => [input.name, input.type === "checkbox" ? input.checked : input.value]));
  }
  function profileFormSignature() {
    return JSON.stringify(Array.from(form.elements).filter((input) => input.name).map((input) => [input.name, input.value, input.type === "checkbox" ? input.checked : null]));
  }
  function intakeContext() {
    const values = readProfile();
    return { paragraph: $("#profile-intake-text").value, signature: profileFormSignature(), confirmed: JSON.stringify(state.intakeConfirmedPatch), values, ...meta() };
  }
  function intakeContextMatches(context) {
    return !state.conflict && context.expected_revision === state.revision && context.generation === state.generation && context.paragraph === $("#profile-intake-text").value && context.signature === profileFormSignature() && context.confirmed === JSON.stringify(state.intakeConfirmedPatch);
  }
  function invalidateIntake(message) {
    const preview = state.intakePreview;
    if (!preview || preview.applied) return;
    preview.stale = true;
    $("#intake-stale").textContent = message; $("#intake-stale").hidden = false; $("#apply-intake").disabled = true;
  }
  function intakeValue(field, value) {
    if (field.startsWith("priorities.")) return { must: "必须", prefer: "偏好", ignore: "忽略" }[value] || "自动";
    if (field === "accepted_schedules") return list(value).map((s) => `${schedules[s.kind] || "安排待确认"}${s.kind === "custom_cycle" ? `（上 ${s.work_days ?? "?"} 天 / 休 ${s.rest_days ?? "?"} 天）` : ""}${list(s.fixed_weekdays).length ? ` · 固定周${s.fixed_weekdays.map((day) => ["", "一", "二", "三", "四", "五", "六", "日"][day]).join("、周")}` : ""}`).join("；") || "不限制休息安排";
    if (field === "priorities") return Object.entries(value || {}).map(([key, priority]) => `${(fieldLabels[`priority.${key}`] || key).replace(/优先级$/, "")}：${{ must: "必须", prefer: "偏好", ignore: "忽略" }[priority] || "自动"}`).join("；") || "自动（明确填写的条件作为必须）";
    const enums = { salary_period: periods, salary_basis: { unspecified: "未指定口径", base: "底薪 / 固定薪资", advertised: "招聘标示薪资" }, insurance_requirement: { any: "不限制", five_insurances: "五险", five_and_fund: "五险一金" } };
    return enums[field]?.[value] || describe(value);
  }
  function intakeQuotes(value, paragraph) {
    const found = [];
    function visit(entry, depth = 0) {
      if (depth > 4) return;
      if (typeof entry === "string" && entry.trim() && paragraph.includes(entry)) found.push(entry);
      else if (Array.isArray(entry)) entry.forEach((item) => visit(item, depth + 1));
      else if (entry && typeof entry === "object") Object.values(entry).forEach((item) => visit(item, depth + 1));
    }
    visit(value); return Array.from(new Set(found));
  }
  function intakePatchValid(field, value) {
    if (arrays.has(field)) return Array.isArray(value) && value.every((item) => typeof item === "string");
    if (numerics.has(field)) return value === null || typeof value === "number" && Number.isFinite(value);
    if (booleans.has(field)) return value === null || typeof value === "boolean";
    if (field === "priorities") return value && typeof value === "object" && !Array.isArray(value) && Object.entries(value).every(([key, priority]) => form.elements.namedItem(`priority.${key}`) && ["must", "prefer", "ignore"].includes(priority));
    if (field === "accepted_schedules") return Array.isArray(value) && value.every((s) => s && Object.hasOwn(schedules, s.kind) && [s.work_days, s.rest_days].every((v) => v == null || Number.isInteger(v) && v > 0) && Array.isArray(s.fixed_weekdays) && s.fixed_weekdays.every((day) => Number.isInteger(day) && day >= 1 && day <= 7));
    if (typeof value !== "string") return false;
    const input = form.elements.namedItem(field);
    return !input || input.tagName !== "SELECT" || Array.from(input.options).some((option) => option.value === value);
  }
  function renderIntakePreview(data, context) {
    if (!data || data.mode !== "llm_profile_preview" || !data.patch || typeof data.patch !== "object" || Array.isArray(data.patch) || !Array.isArray(data.changes) || !Array.isArray(data.questions) || typeof data.summary !== "string") throw new Error("整理服务返回的预览格式不完整，未填入任何条件。请重试。");
    if (data.revision !== context.expected_revision || data.generation !== context.generation) { const error = new Error("整理期间画像版本发生变化，请对照当前版本后重新整理。"); error.status = 409; throw error; }
    const patch = { ...data.patch };
    for (const [field, value] of Object.entries(patch)) {
      if ((!Object.hasOwn(context.values, field) && field !== "city") || !intakePatchValid(field, value)) throw new Error("整理结果含有无法安全填入表单的字段，未改动任何草稿。请重试或逐项填写。");
    }
    if (Object.hasOwn(patch, "city")) {
      if (!Object.hasOwn(patch, "cities")) patch.cities = patch.city ? [patch.city] : [];
      else if (patch.city !== (patch.cities[0] || "")) throw new Error("整理结果中的城市信息不一致，未填入任何条件。请重新整理。");
      delete patch.city;
    }
    const preview = { context, rows: [], stale: !intakeContextMatches(context), applied: false };
    const area = $("#intake-changes"); area.replaceChildren();
    const groups = new Map();
    const groupTitle = (field) => ["cities", "districts", "target_roles", "salary_min", "salary_max", "salary_period", "salary_basis"].includes(field) ? "地点、岗位与薪资" : ["insurance_requirement", "insurance_during_probation", "fund_base_min", "accepted_schedules"].includes(field) ? "保障与休息" : ["name", "skills", "experience_years", "education", "notes"].includes(field) ? "个人情况与说明" : field.startsWith("priorities.") ? "条件优先级" : "工作方式与其他要求";
    const entries = Object.entries(patch).flatMap(([field, value]) => field === "priorities" ? Object.entries(value).map(([key, priority]) => [`priorities.${key}`, priority]) : [[field, value]]);
    for (const [field, value] of entries) {
      const aliases = field === "cities" ? ["cities", "city"] : [field];
      const change = data.changes.find((item) => item && aliases.includes(item.field));
      const priority = field.startsWith("priorities.") ? field.slice(11) : null;
      const before = priority ? context.values.priorities[priority] || "" : context.values[field];
      const label = priority ? fieldLabels[`priority.${priority}`] || "条件优先级" : fieldLabels[field] || field;
      const priorityEvidence = priority ? [data.evidence?.priorities?.[priority], data.changes.find((item) => item?.field === "priorities")?.evidence] : [];
      const quotes = intakeQuotes([change?.evidence, ...aliases.map((key) => data.evidence?.[key]), ...priorityEvidence], context.paragraph);
      const title = groupTitle(field);
      if (!groups.has(title)) { const section = node("fieldset", "intake-change-group"); section.append(node("legend", "", title)); area.append(section); groups.set(title, section); }
      const row = node("label", "intake-change"), input = node("input"), content = node("span", "intake-change-content");
      input.type = "checkbox"; input.checked = !!quotes.length; input.disabled = !quotes.length;
      content.append(node("strong", "", label), node("span", "intake-before", `当前：${intakeValue(field, before)}`), node("span", "intake-after", `${equal(before, value) ? "明确确认" : "整理为"}：${intakeValue(field, value)}`));
      if (quotes.length) quotes.forEach((quote) => content.append(node("q", "intake-quote", quote)));
      else content.append(node("small", "", "未提供可核对的原文引用，本项不会填入。请补充说明后重新整理。"));
      row.append(input, content); groups.get(title).append(row);
      preview.rows.push({ field, value, input }); input.addEventListener("change", syncControls);
    }
    $("#intake-summary").textContent = data.summary || "已按原文整理。请核对每项原文引用，只勾选你确认的内容。";
    const questions = $("#intake-questions"); questions.replaceChildren();
    if (data.questions.length) { questions.append(node("h4", "", "还需要你补充")); const ul = node("ul"); data.questions.forEach((question) => ul.append(node("li", "", text(question)))); questions.append(ul); }
    if (!preview.rows.length) area.append(node("p", "field-help", "这段话没有可填入的新条件或明确确认项。现有条件保持不变，可按提示补充城市、岗位、薪资或上休要求后再整理。"));
    state.intakePreview = preview;
    $("#intake-preview").hidden = false; $("#intake-stale").hidden = true; $("#discard-intake").textContent = "放弃此预览"; $("#intake-preview-state").textContent = "尚未保存";
    if (preview.stale) invalidateIntake("原文、条件草稿或画像版本已变化。旧预览仅供对照，请重新整理后再填入。");
    $("#intake-status").textContent = preview.stale ? "条件已变，请重新整理。" : preview.rows.some((row) => !row.input.disabled) ? "请核对并填入，尚未保存。" : preview.rows.length ? "原话依据不足，请补充或重试。" : "暂无变化，请查看补充问题。";
    syncControls();
  }
  function fillIntakePatch(patch) {
    for (const [field, value] of Object.entries(patch)) {
      if (field === "accepted_schedules") {
        form.querySelectorAll("[name^='schedule.']").forEach((input) => { input.checked = value.some((s) => s.kind === input.name.slice(9)); });
        form.querySelectorAll("[name^='weekday.']").forEach((input) => { input.checked = value.filter((s) => s.kind !== "weekend_double").some((s) => s.fixed_weekdays.includes(Number(input.name.slice(8)))); });
        const custom = value.find((s) => s.kind === "custom_cycle");
        form.elements.namedItem("cycle.work_days").value = custom?.work_days ?? "";
        form.elements.namedItem("cycle.rest_days").value = custom?.rest_days ?? "";
        state.scheduleDraft = { value, signature: scheduleSignature() };
      } else if (field === "priorities") {
        Object.entries(value).forEach(([key, priority]) => { form.elements.namedItem(`priority.${key}`).value = priority; });
      } else {
        const input = form.elements.namedItem(field);
        input.value = arrays.has(field) ? value.join("、") : value == null ? "" : String(value);
      }
    }
  }
  function applyIntakePreview() {
    const preview = state.intakePreview;
    if (!preview || preview.applied || preview.stale || state.busy || state.intakeController) return;
    if (!intakeContextMatches(preview.context)) { invalidateIntake("当前原文、条件草稿或版本已变化，请重新整理。未覆盖新编辑。"); return; }
    const selected = preview.rows.filter((row) => row.input.checked && !row.input.disabled);
    if (!selected.length) { $("#intake-status").textContent = "请至少勾选一项有原文依据的条件，或放弃此预览。"; return; }
    const patch = {};
    selected.forEach((row) => { if (row.field.startsWith("priorities.")) { patch.priorities ||= {}; patch.priorities[row.field.slice(11)] = row.value; } else patch[row.field] = row.value; });
    fillIntakePatch(patch);
    const priorities = { ...state.intakeConfirmedPatch.priorities, ...patch.priorities };
    Object.assign(state.intakeConfirmedPatch, patch);
    if (patch.priorities) state.intakeConfirmedPatch.priorities = priorities;
    if (Object.hasOwn(patch, "cities")) state.intakeConfirmedPatch.city = patch.cities[0] || "";
    preview.applied = true; preview.stale = true;
    preview.rows.forEach((row) => { row.input.disabled = true; });
    $("#intake-stale").hidden = true; $("#discard-intake").textContent = "收起此预览"; $("#intake-preview-state").textContent = "已填入草稿";
    markDirty(); updateProfileSummary();
    profileStatus(`已填入 ${selected.length} 项并保留其他草稿 · 尚未保存。核对后请点击「保存条件」。`);
    $("#intake-status").textContent = "已填入草稿，请保存。";
    syncControls();
  }
  async function interpretProfile() {
    if (state.intakeController || state.busy || !state.loaded || state.conflict) return;
    const context = intakeContext(), paragraph = context.paragraph.trim();
    if (!paragraph || context.paragraph.length > 20000) { $("#intake-status").textContent = "请填写 1 至 20000 字的要求描述。原文不会自动保存。"; return; }
    invalidateIntake("正在按当前原文重新整理，请等待新预览。旧预览未改动条件。");
    const payload = { text: context.paragraph, draft_patch: profilePatch(), expected_revision: context.expected_revision, generation: context.generation };
    const controller = new AbortController(); state.intakeController = controller;
    const timeout = setTimeout(() => controller.abort("timeout"), 120000);
    $("#intake-status").textContent = "正在整理，尚未保存…";
    $("#interpret-profile").textContent = "正在整理…"; syncControls();
    try {
      const response = await fetch("/api/profile/interpret", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal });
      if (response.status === 404) { const error = new Error("后端仍是旧版本，请重启本地服务后再试"); error.status = 404; throw error; }
      let data; try { data = await response.json(); } catch { throw new Error("整理服务未返回完整 JSON 预览，请重试；原文仍保留。"); }
      if (state.intakeController !== controller) return;
      if (controller.signal.aborted) throw new DOMException("整理已取消", "AbortError");
      if (!response.ok) {
        const hints = { 400: "请检查描述内容后重试。", 422: "请检查原文长度或条件草稿格式。", 428: "请先完成模型配置。", 502: "模型未返回有效条件；可补充说明后重试，没有使用替代结果。", 409: "画像或数据版本已变化，请先对照当前条件。" };
        const detail = text(data?.detail, "本次整理未完成。");
        const error = new Error(`${detail.length > 1500 || detail.includes("Traceback") ? "整理服务暂时不可用。" : detail} ${hints[response.status] || "请稍后重试。"}`); error.status = response.status; throw error;
      }
      renderIntakePreview(data, context);
    } catch (error) {
      if (state.intakeController !== controller) return;
      const message = controller.signal.aborted ? controller.signal.reason === "timeout" ? "整理等待超时" : "已取消整理" : error instanceof TypeError ? "无法连接整理服务，请检查本地服务或模型连接" : error.message;
      $("#intake-status").textContent = `${message}。原文和条件草稿仍保留，未自动保存或搜索，可以重试。`;
      if (error.status === 428) showDialog("#model-dialog");
      if (error.status === 409) { invalidateIntake("画像版本已变化，请对照版本后重新整理。原文与草稿仍保留。"); markConflict(); }
    } finally {
      clearTimeout(timeout);
      if (state.intakeController === controller) { state.intakeController = null; $("#interpret-profile").textContent = "帮我整理要求"; syncControls(); }
    }
  }

  function readProfile() {
    const values = {}, priorities = {};
    for (const input of form.elements) {
      if (!input.name || input.name.startsWith("schedule.") || input.name.startsWith("cycle.") || input.name.startsWith("weekday.")) continue;
      const name = input.name, value = input.value.trim();
      if (name.startsWith("priority.")) { if (value) priorities[name.slice(9)] = value; continue; }
      values[name] = arrays.has(name) ? split(value) : numerics.has(name) ? (value === "" ? name === "salary_min" ? 0 : null : Number(value)) : booleans.has(name) ? value === "" ? null : value === "true" : value;
    }
    values.priorities = priorities;
    const fixed = Array.from(form.querySelectorAll("[name^='weekday.']:checked")).map((input) => Number(input.name.slice(8)));
    values.accepted_schedules = Object.keys(schedules).filter((kind) => form.elements.namedItem(`schedule.${kind}`).checked).map((kind) => {
      const old = list(state.profile.accepted_schedules).find((s) => s.kind === kind);
      const custom = kind === "custom_cycle";
      return { kind, work_days: custom ? Number(form.elements.namedItem("cycle.work_days").value) || null : old?.work_days ?? null, rest_days: custom ? Number(form.elements.namedItem("cycle.rest_days").value) || null : old?.rest_days ?? null, fixed_weekdays: kind === "weekend_double" ? [6, 7] : fixed };
    });
    if (state.scheduleDraft && state.scheduleDraft.signature === scheduleSignature()) values.accepted_schedules = state.scheduleDraft.value;
    return values;
  }
  function profilePatch(confirmLegacy = false) {
    const now = readProfile(), patch = {};
    // Explicitly accepted equal-valued fields must also reach PATCH to confirm legacy data.
    Object.keys(state.intakeConfirmedPatch).forEach((key) => { if (key === "city") patch.city = now.cities[0] || ""; else if (Object.hasOwn(now, key)) patch[key] = now[key]; });
    Object.entries(now).forEach(([key, value]) => { if (!equal(value, state.baseline[key])) patch[key] = value; });
    // A paragraph approves only its selected fields, not unrelated legacy defaults.
    if (confirmLegacy && !Object.keys(state.intakeConfirmedPatch).length) {
      const pending = list(state.profile.confirmation_required);
      pending.forEach((key) => { if (Object.hasOwn(now, key)) patch[key] = now[key]; });
      if (pending.includes("city")) { patch.cities = now.cities; patch.city = now.cities[0] || ""; }
      if (pending.includes("require_double_rest")) patch.accepted_schedules = now.accepted_schedules;
      if (pending.includes("require_housing_fund")) patch.insurance_requirement = now.insurance_requirement;
    }
    if (Object.hasOwn(patch, "cities")) patch.city = now.cities[0] || "";
    return patch;
  }
  function profileStatus(message) { $("#profile-status").textContent = message; }
  function updateProfileSummary() {
    const p = state.profile;
    const values = [list(p.cities).join("、") || p.city, list(p.target_roles).join("、"), p.salary_min ? `≥${p.salary_min} 元/${periods[p.salary_period] || "月"}` : "薪资不限", { five_and_fund: "五险一金", five_insurances: "五险" }[p.insurance_requirement], ...list(p.accepted_schedules).map((s) => schedules[s.kind])].filter(Boolean);
    $("#profile-summary").textContent = `${values.join(" · ") || "尚未设置条件"}${state.conflict ? " · 需要对照版本" : state.dirty ? " · 有未保存草稿" : ""}`;
  }
  function markDirty() {
    if (!state.loaded) return;
    invalidateIntake("条件草稿已变化。原预览仍可对照，请重新整理后再填入，避免覆盖新编辑。");
    state.dirty = true;
    profileStatus(state.conflict ? "版本冲突 · 草稿保留，请对照当前画像。" : "有未保存草稿 · 分析使用保存的版本；保存不会自动搜索。");
    clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(() => { state.dirty = Object.keys(profilePatch()).length > 0; updateProfileSummary(); if (!state.dirty && !state.conflict) profileStatus("与已保存条件一致。"); }, 450);
  }
  function restorePreview(profile, preview) {
    // Resume files are deferred in the current workflow; legacy data is preserved.
  }
  function applySnapshot(data, preserveEdits = false) {
    if (!data.profile || !Number.isInteger(data.revision) || !Number.isInteger(data.generation)) throw new Error("画像响应缺少版本信息，请刷新后重试。");
    if (data.revision !== state.revision || data.generation !== state.generation) invalidateIntake("已保存条件的版本发生变化，请重新整理原文。原预览未自动填入。");
    if (preserveEdits && (state.dirty || state.conflict || Object.keys(state.intakeConfirmedPatch).length)) {
      if (data.generation !== state.generation || !equal(state.profile, data.profile) || state.conflict) { state.current = data; markConflict(); }
      else { state.revision = data.revision; state.generation = data.generation; profileStatus("分析已保存 · 你正在编辑的条件草稿仍保留，尚未保存。"); }
      updateProfileSummary(); return;
    }
    clearTimeout(dirtyTimer);
    if (!preserveEdits) {
      state.intakeConfirmedPatch = {}; state.scheduleDraft = null;
      if (state.intakePreview?.applied) { $("#intake-preview-state").textContent = "历史预览"; $("#intake-status").textContent = "条件表单已读取已保存的版本，原文与历史整理预览仍保留供对照。"; }
    }
    state.profile = data.profile; state.revision = data.revision; state.generation = data.generation; state.loaded = true;
    const p = data.profile;
    for (const input of form.elements) {
      const name = input.name; if (!name) continue;
      if (name.startsWith("schedule.")) { input.checked = list(p.accepted_schedules).some((s) => s.kind === name.slice(9)); continue; }
      if (name.startsWith("weekday.")) { input.checked = list(p.accepted_schedules).filter((s) => s.kind !== "weekend_double").some((s) => list(s.fixed_weekdays).includes(Number(name.slice(8)))); continue; }
      let value;
      if (name.startsWith("priority.")) {
        const legacy = { location: ["city", "cities"], salary: ["salary_min", "salary_basis"], insurance: ["insurance_requirement", "require_housing_fund"], schedule: ["accepted_schedules", "require_double_rest"], remote: ["allow_remote"] };
        const group = name.slice(9);
        value = (legacy[group] || []).some((key) => list(p.confirmation_required).includes(key)) ? "" : p.priorities?.[group] || "";
      }
      else if (name.startsWith("cycle.")) value = list(p.accepted_schedules).find((s) => s.kind === "custom_cycle")?.[name.slice(6)] ?? "";
      else if (name === "cities") value = list(p.cities).length ? p.cities : p.city ? [p.city] : [];
      else value = p[name] ?? ({ salary_basis: "unspecified", salary_period: "month", insurance_requirement: "any" }[name] || "");
      if (arrays.has(name)) value = list(value).join("、");
      if (typeof value === "boolean") value = String(value);
      if (input.tagName === "SELECT" && value !== "" && !Array.from(input.options).some((option) => option.value === String(value))) input.add(new Option(String(value), String(value)));
      input.value = value;
    }
    state.scheduleDraft = { value: list(p.accepted_schedules), signature: scheduleSignature() };
    state.baseline = readProfile(); state.dirty = false; state.conflict = false; state.current = null;
    $("#profile-conflict").hidden = true; $("#conflict-preview").replaceChildren(); $("#use-current-profile").hidden = true;
    restorePreview(p, data.preview);
    const pending = list(p.confirmation_required);
    profileStatus(`已保存 · ${timestamp(data.updated_at)}${pending.length ? " · 旧条件待确认，暂不筛选" : ""}`);
    updateProfileSummary();
  }
  function markConflict() { state.conflict = true; invalidateIntake("画像版本存在冲突，请对照当前版本后重新整理。原文和条件草稿仍保留。"); $("#profile-conflict").hidden = false; $("#profile-editor").open = true; profileStatus("版本冲突 · 本页编辑仍保留，未覆盖当前画像。"); updateProfileSummary(); }
  async function saveDirty(confirmLegacy = false) {
    if (state.chatController) throw new Error("分析生成中，请结束后再保存。条件草稿会保留。");
    if (!state.loaded) throw new Error("画像尚未载入，不能保存。");
    if (state.conflict) throw new Error("请先对照版本冲突。你的编辑仍在本页。");
    const intakeSave = !!Object.keys(state.intakeConfirmedPatch).length;
    const patch = profilePatch(confirmLegacy); if (!Object.keys(patch).length) { state.dirty = false; updateProfileSummary(); return; }
    if (!form.reportValidity()) throw new Error("请检查画像中的数字和时间。");
    for (const input of form.querySelectorAll("input[type=number]")) {
      if (input.value === "") continue;
      const value = Number(input.value);
      if (!Number.isFinite(value) || (input.min !== "" && value < Number(input.min)) || (input.max !== "" && value > Number(input.max)) || (input.step === "1" && !Number.isInteger(value))) throw new Error("数字超出允许范围，请检查薪资、工时、经验与上休天数。");
    }
    const p = readProfile();
    if (p.salary_max !== null && p.salary_max < p.salary_min) throw new Error("最高薪资不能低于最低薪资。");
    const custom = p.accepted_schedules.find((s) => s.kind === "custom_cycle");
    if (custom && (!custom.work_days || !custom.rest_days)) throw new Error("自定义上休周期请同时填写上班天数和休息天数。");
    profileStatus("保存中…");
    try {
      applySnapshot(await api("/api/profile", { method: "PATCH", body: { patch, ...meta() } }));
      if (state.intakePreview?.applied) { $("#intake-preview-state").textContent = "已保存"; $("#intake-status").textContent = "条件已保存，可查岗位。"; }
      if (intakeSave && list(state.profile.confirmation_required).length) profileStatus("已保存本次确认项 · 其他历史条件仍待确认，继续忽略，不作为硬条件。");
      await refreshJobs();
    }
    catch (error) { if (error.status === 409) markConflict(); else profileStatus(`未保存 · ${error.message}`); throw error; }
  }

  function renderMarkdown(body, content) {
    if (typeof window.FanwanMarkdown?.render === "function") window.FanwanMarkdown.render(body, text(content));
    else { body.classList.add("markdown-body"); body.dataset.markdownMode = "plain"; body.replaceChildren(node("p", "", content)); }
    if (text(content)) $("#markdown-status").hidden = body.dataset.markdownMode === "marked";
  }
  function renderMessages(messages, follow = true) {
    const area = $("#messages"), position = area.scrollTop; area.replaceChildren();
    const visible = list(messages).filter((m) => ["user", "assistant"].includes(m?.role));
    if (!visible.length) area.append(node("p", "chat-intro", "选好岗位，聊聊匹配、取舍和待问问题。"));
    visible.forEach((m) => appendMessage(m.role, m.content)); area.scrollTop = follow ? area.scrollHeight : position;
  }
  function appendMessage(role, content) { const el = node("div", `message ${role === "user" ? "user" : "assistant"}`), body = node("div", "message-content"); if (role === "user") body.textContent = text(content); else renderMarkdown(body, content); el.append(node("span", "message-label", role === "user" ? "你" : "饭碗助手"), body); $("#messages").append(el); return { el, body }; }

  function renderSelection() {
    const area = $("#selected-jobs"), signature = JSON.stringify(Array.from(state.selectedJobs, ([id, job]) => [id, job.title, job.company]));
    if (signature !== selectionSignature) {
      area.replaceChildren(); selectionSignature = signature;
      state.selectedJobs.forEach((job, id) => {
      const button = node("button", "job-chip", `${job.title || "未命名职位"} · ${job.company || "公司待确认"} ×`);
      button.type = "button"; button.setAttribute("aria-label", `移除分析职位：${job.title || id}`);
      button.addEventListener("click", () => { state.selectedJobs.delete(id); renderSelection(); }); area.append(button);
      });
    }
    $("#selection-status").textContent = `${state.selectedJobs.size ? `已选 ${state.selectedJobs.size} / 5 份` : "未选择时，分析已有岗位。"}${state.pendingChat ? " 新选择下次生效。" : ""}`;
    document.querySelectorAll(".job-select").forEach((input) => { input.checked = state.selectedJobs.has(input.dataset.jobId); });
    syncControls();
  }
  function selectJob(job, selected) {
    if (job.id == null) return false;
    const id = String(job.id);
    if (selected && !state.selectedJobs.has(id) && state.selectedJobs.size >= 5) { notify("最多选择 5 份已有职位，请先移除一份。", "error"); return false; }
    if (selected) state.selectedJobs.set(id, job); else state.selectedJobs.delete(id);
    renderSelection(); return true;
  }
  function focusAnalysis(message) {
    if (state.pendingChat) { notify("请先重试或结束尚未完成的分析，再提出新问题。", "error"); }
    else $("#chat-input").value = message;
    $("#analysis-panel").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); $("#chat-input").focus({ preventScroll: true });
  }

  function chatError(data, status) {
    const detail = text(data?.detail, "模型服务未能完成分析，请稍后重试。");
    const hints = { 400: "请核对问题和所选职位。", 401: "请在模型设置中检查服务鉴权。", 428: "请先完成模型配置。", 409: "画像版本已变化，请对照当前版本；不要覆盖未保存编辑。" };
    const error = new Error(`${detail.length > 1500 || detail.includes("Traceback") ? "模型服务暂时无法完成分析。" : detail}${hints[status] ? ` ${hints[status]}` : ""}`);
    error.status = status; error.code = data?.code; return error;
  }

  async function streamChat(payload, signal, onDelta) {
    const response = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify(payload), signal });
    if (!response.ok) { let data = {}; try { data = await response.json(); } catch { /* Preserve the HTTP status when the body is not JSON. */ } throw chatError(data, response.status); }
    if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("服务未返回可读取的分析事件流，请检查后重试。");
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", event = "message", lines = [], done = null;
    function dispatch() {
      const kind = event, raw = lines.join("\n"); event = "message"; lines = [];
      if (!raw) return;
      let data; try { data = JSON.parse(raw); } catch { throw new Error("分析事件格式不完整，请重试本次请求。"); }
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("分析事件内容无效，请重试本次请求。");
      if (kind === "error") throw chatError(data, Number(data.http_status) || 502);
      if (kind === "start" && data.request_id && data.request_id !== payload.request_id) throw new Error("分析请求编号不一致，请重试。");
      if (kind === "delta") { if (typeof data.text !== "string") throw new Error("分析片段格式无效。"); onDelta(data.text); }
      if (kind === "done") {
        if (data.mode !== "llm_stream" || typeof data.reply !== "string" || !data.profile || !Number.isInteger(data.revision) || !Number.isInteger(data.generation) || data.generation !== payload.generation || data.generation !== state.generation) throw new Error("分析完成事件缺少有效保存信息，请重试确认。");
        done = data;
      }
    }
    function consume(final = false) {
      // Parse SSE lines across arbitrary UTF-8 chunks, including split CRLF boundaries.
      let end;
      while (!done && (end = buffer.search(/[\r\n]/)) !== -1) {
        if (buffer[end] === "\r" && end === buffer.length - 1 && !final) break;
        const line = buffer.slice(0, end), width = buffer[end] === "\r" && buffer[end + 1] === "\n" ? 2 : 1;
        buffer = buffer.slice(end + width);
        if (!line) { dispatch(); continue; }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":"), field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1); if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") event = value; else if (field === "data") lines.push(value);
      }
    }
    try {
      while (!done) {
        const chunk = await reader.read();
        buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true }); consume(chunk.done);
        if (chunk.done) break;
      }
      if (!done) throw new Error("连接结束但未收到保存完成确认，已收到的片段尚未确认保存。");
      return done;
    } finally { try { await reader.cancel(); } catch { /* The stream may already be disconnected. */ } reader.releaseLock(); }
  }

  async function runAnalysis() {
    if (state.busy || state.chatController || !state.loaded) return;
    const payload = state.pendingChat; if (!payload) return;
    if (!state.chatDraft) {
      $("#messages .chat-intro")?.remove();
      const user = appendMessage("user", payload.message), assistant = appendMessage("assistant", "");
      const status = node("small", "partial-status", "正在生成 · 尚未保存"); assistant.el.append(status);
      state.chatDraft = { user, assistant, status };
    }
    const draft = state.chatDraft;
    window.FanwanMarkdown?.reset?.(draft.assistant.body); draft.assistant.body.replaceChildren(); draft.status.textContent = "正在请求真实模型 · 尚未保存";
    draft.assistant.el.classList.add("partial"); draft.user.el.classList.add("partial");
    const controller = new AbortController(); state.chatController = controller; syncControls();
    $("#chat-status").textContent = "正在分析发送时保存的现状与职位。可继续编辑条件草稿，完成后再保存。";
    let queued = "", received = "", flushTimer = null;
    const flush = () => {
      clearTimeout(flushTimer); flushTimer = null;
      if (!queued) return;
      const area = $("#messages"), follow = area.scrollHeight - area.scrollTop - area.clientHeight < 64;
      received += queued; queued = ""; renderMarkdown(draft.assistant.body, received);
      draft.status.textContent = "真实模型生成中 · 尚未保存";
      if (follow) area.scrollTop = area.scrollHeight;
    };
    try {
      const data = await streamChat(payload, controller.signal, (token) => {
        queued += token;
        if (flushTimer === null) flushTimer = setTimeout(flush, 50);
      });
      flush();
      const area = $("#messages"), follow = area.scrollHeight - area.scrollTop - area.clientHeight < 64;
      // A cached completion may predate a profile saved after the interrupted attempt.
      const olderSnapshot = data.revision < state.revision;
      if (!olderSnapshot) applySnapshot(data, true);
      if (!olderSnapshot && Array.isArray(data.messages)) renderMessages(data.messages, follow);
      else { draft.user.el.classList.remove("partial"); draft.assistant.el.classList.remove("partial"); renderMarkdown(draft.assistant.body, data.reply); draft.status.remove(); }
      state.pendingChat = null; state.chatDraft = null; $("#chat-input").value = "";
      $("#chat-status").textContent = `${data.replayed ? "已确认本次分析此前已保存；未重复新增对话。" : "分析已完整保存，求职条件保持不变。"}${olderSnapshot ? " 本次回复基于此前的画像，新保存的画像仍保留。" : ""}${state.dirty ? " 正在编辑的条件草稿仍保留，尚未保存。" : ""}`;
      $("#assistant-mode").textContent = `${state.model.model || "已配置模型"}`;
      $("#chat-suggestions").hidden = true;
      if (follow) area.scrollTop = area.scrollHeight;
    } catch (error) {
      flush();
      const message = error.name === "AbortError" ? "已停止生成" : error instanceof TypeError ? "分析连接中断，请检查本地服务或模型连接" : error.message;
      draft.status.textContent = `${message} · 本次片段未保存（未收到完成确认）`;
      $("#chat-status").textContent = `${message}。本次内容未确认保存，之前成功的对话仍保留。重试将沿用同一请求编号及原始上下文，确认后台是否已保存。`;
      if (error.status === 401 || error.status === 428) { $("#assistant-mode").textContent = error.status === 428 ? "缺少模型配置 · 请先设置" : "模型鉴权失败 · 请检查设置"; if (error.status === 428) showDialog("#model-dialog"); }
      if (error.status === 409) markConflict();
    } finally { clearTimeout(flushTimer); state.chatController = null; renderSelection(); }
  }
  function safeURL(value) { try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; } }
  function link(value, title) { const url = safeURL(value); if (!url) return node("span", "field-help", "未提供有效原始链接"); const a = node("a", "source-link", title); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; return a; }
  const truth = (v, yes, no, unknown) => v === true ? yes : v === false ? no : unknown;
  const ruleFieldLabels = { location: "地点", salary: "薪资", insurance: "社保与公积金", schedule: "休息安排", hours: "工时", social_insurance: "社保", housing_fund: "公积金", fund_base: "公积金缴纳基数", schedule_kind: "休息安排类型", work_hours_daily: "每日工作时长", night_shift: "夜班", rotating_shift: "倒班", overtime: "加班", outsourced: "外包", dispatched: "劳务派遣", employment_type: "用工形式", industry: "行业", salary_text: "薪资原文", salary_source: "薪资依据", description: "职位描述", benefits: "福利说明", welfare: "福利说明", rest: "休息安排", education: "学历", experience: "工作经验", remote: "远程办公", base: "底薪 / 固定薪资", actual: "岗位信息", expected: "你的条件", required: "要求", evidence: "依据", completeness: "信息完整度", source: "来源", source_url: "原始链接", insurance_during_probation: "试用期缴纳", cycle_work_days: "周期上班天数", cycle_rest_days: "周期休息天数", weekly_rest_days: "每周休息天数", fixed_rest_weekdays: "固定休息日" };
  const ruleEnums = { ...schedules, ...periods, None: "待确认", null: "待确认", unknown: "待确认", unspecified: "口径未指定", True: "是", true: "是", False: "否", false: "否", base: "底薪 / 固定薪资", advertised: "招聘标示薪资", any: "不限制", five_insurances: "五险", five_and_fund: "五险一金", must: "必须", prefer: "偏好", ignore: "忽略", full_jd: "完整职位原文", list_only: "仅列表信息", search_snippet: "搜索摘要", user_provided: "用户提供 / 未核验" };
  function ownLabel(labels, key) { return Object.hasOwn(labels, key) && typeof labels[key] === "string" ? labels[key] : ""; }
  function ruleFieldLabel(key) { return ownLabel(ruleFieldLabels, key) || ownLabel(fieldLabels, key) || (/^[a-z_]+$/.test(key) ? "来源说明" : key); }
  function ruleValue(value, depth = 0) {
    if (value == null) return "待确认";
    if (typeof value === "boolean") return value ? "是" : "否";
    if (depth > 3) return "详细结构见原始说明";
    if (Array.isArray(value)) return value.map((item) => ruleValue(item, depth + 1)).join("、") || "未设置或未提供";
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${ruleFieldLabel(key)}：${ruleValue(item, depth + 1)}`).join("；");
    return ownLabel(ruleEnums, value) || text(value);
  }
  function ruleLiteralList(raw) {
    const inner = raw.slice(1, -1), pattern = /'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|\b(?:None|null|True|False|true|false)\b|-?\d+(?:\.\d+)?/g;
    if (!inner.trim()) return "未设置或未提供";
    const tokens = inner.match(pattern);
    if (!tokens || inner.replace(pattern, "").replace(/[\s,]/g, "")) return raw;
    return tokens.map((token) => ruleValue(/^["']/.test(token) ? token.slice(1, -1).replace(/\\([\\'"])/g, "$1") : token)).join("、");
  }
  function ruleExplanation(value) {
    if (typeof value !== "string") return ruleValue(value);
    // This formatter is used only for generated rule explanations, never source text.
    return value.replace(/\[[^\]\r\n]{0,600}\]|“[^”]*”|「[^」]*」|『[^』]*』|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`|\b[A-Za-z_][A-Za-z0-9_]*\b/g, (token) => {
      if (token.startsWith("[")) return ruleLiteralList(token);
      if (/^[“「『"'`]/.test(token)) return token;
      return ownLabel(ruleEnums, token) || ownLabel(ruleFieldLabels, token) || ownLabel(fieldLabels, token) || token;
    });
  }
  function renderRuleCheck(parent, check) {
    const row = node("div", "check"), body = node("div"), status = ["pass", "fail"].includes(check.status) ? check.status : "unknown";
    const raw = check.evidence, readable = raw == null || raw === "" ? "无明确来源依据，仍需进一步确认。" : ruleExplanation(raw);
    row.append(node("span", status, { pass: "✓", fail: "×", unknown: "?" }[status]));
    body.append(node("strong", "", `${ruleExplanation(check.label || "条件")} · ${{ pass: "符合", fail: "不符", unknown: "待确认" }[status]}`), node("p", "", readable));
    if (raw != null && (typeof raw !== "string" || readable !== raw)) {
      const original = node("details", "rule-raw"); original.append(node("summary", "", "查看规则原始说明"), node("pre", "preview", typeof raw === "string" ? raw : JSON.stringify(raw, null, 2))); body.append(original);
    }
    row.append(body); parent.append(row);
  }
  function detailList(parent, title, items, format = (value) => text(value)) { if (!list(items).length) return; const section = node("section", "detail-section"); section.append(node("h4", "", title)); const ul = node("ul"); list(items).forEach((v) => ul.append(node("li", "", format(v)))); section.append(ul); parent.append(section); }
  function jobCard(job) {
    const analysis = job.analysis || {}, verdict = verdicts[analysis.verdict] ? analysis.verdict : "needs_confirmation";
    const card = node("article", "job-card"), top = node("div", "job-top");
    card.dataset.jobKey = text(job.id || job.source_url || `${job.source}:${job.company}:${job.title}`);
    const salary = job.salary_text || (job.salary_min ? `${job.salary_min}${job.salary_max ? `–${job.salary_max}` : " 起"} 元` : "薪资待确认");
    top.append(node("h3", "", job.title || "未命名职位"), node("span", "salary", `${salary} · ${periods[job.salary_period] || "周期待确认"}`));
    const completeness = { full_jd: "完整 JD", list_only: "仅列表 · 详情未取得", search_snippet: "搜索摘要 · 未核验", user_provided: "用户提供 · 未核验" };
    const badges = node("div", "badges");
    [sourceNames[job.source] || text(job.source, "来源待确认"), job.source === "import" ? completeness.user_provided : completeness[job.completeness] || "完整度待确认", job.education, job.employment_type, job.industry].filter(Boolean).forEach((s) => badges.append(node("span", "badge", s)));
    const rest = schedules[job.schedule_kind] || "休息安排待确认";
    const facts = [truth(job.social_insurance, "有社保", "无社保", "社保待确认"), truth(job.housing_fund, "有公积金", "无公积金", "公积金待确认"), truth(job.insurance_during_probation, "试用期缴纳", "试用期不缴纳", "试用期缴纳待确认"), rest];
    if (job.cycle_work_days != null && job.cycle_rest_days != null) facts.push(`上 ${job.cycle_work_days} 天 / 休 ${job.cycle_rest_days} 天`);
    if (job.weekly_rest_days != null) facts.push(`每周休 ${job.weekly_rest_days} 天`);
    if (list(job.fixed_rest_weekdays).length) facts.push(`固定休：${job.fixed_rest_weekdays.map((d) => `周${["", "一", "二", "三", "四", "五", "六", "日"][d] || "?"}`).join("、")}`);
    if (job.work_hours_daily != null) facts.push(`每日 ${job.work_hours_daily} 小时`);
    if (job.work_start || job.work_end) facts.push(`${job.work_start || "?"}–${job.work_end || "?"}`);
    if (job.fund_base != null) facts.push(`公积金基数 ${job.fund_base}`);
    facts.push(truth(job.night_shift, "有夜班", "无夜班", "夜班待确认"), truth(job.rotating_shift, "有倒班", "无倒班", "倒班待确认"), truth(job.overtime, "有加班", "无加班", "加班待确认"));
    const details = node("details", "job-details"); details.append(node("summary", "", "查看依据与待确认项"));
    let detailsBuilt = false;
    details.addEventListener("toggle", () => {
      if (!details.open || detailsBuilt) return;
      detailsBuilt = true;
      details.append(node("p", "job-facts", facts.join(" · ")));
      const sourceEvidence = node("section", "source-evidence"); sourceEvidence.append(node("h4", "", "来源原文证据（保留原文）"));
      Object.entries(job.evidence || {}).forEach(([key, value]) => { if (typeof value === "string" && value) { const item = node("div"); item.append(node("strong", "", ruleFieldLabel(key)), node("blockquote", "evidence", value)); item.title = key; sourceEvidence.append(item); } });
      if (sourceEvidence.childElementCount > 1) details.append(sourceEvidence);
      list(analysis.checks).forEach((check) => { if (check && typeof check === "object") renderRuleCheck(details, check); });
      detailList(details, "匹配亮点", analysis.strengths, ruleExplanation); detailList(details, "信息缺口", analysis.gaps, ruleExplanation); detailList(details, "建议确认", analysis.questions, ruleExplanation);
      if (job.description) details.append(node("p", "job-description", job.description));
      if (job.salary_source) details.append(node("p", "field-help", `薪资依据：${job.salary_source}`));
      details.append(node("p", "field-help", `发布时间：${job.posted_at ? text(job.posted_at) : "未知"} · 采集时间：${job.collected_at ? timestamp(job.collected_at) : "未知"}`));
      const apply = node("button", "button secondary application-button", "准备沟通草稿"); apply.type = "button"; apply.dataset.missingId = String(job.id == null); apply.disabled = state.busy || !!state.chatController || job.id == null; apply.addEventListener("click", () => createApplication(job, apply)); details.append(apply);
    });
    const bottom = node("div", "job-bottom"), original = link(job.source_url, "查看原始职位"); original.classList.add("source-action"); bottom.append(original);
    const selection = node("label", "checkbox-label job-selection"), checkbox = node("input");
    checkbox.type = "checkbox"; checkbox.className = "job-select"; checkbox.dataset.jobId = text(job.id); checkbox.dataset.missingId = String(job.id == null);
    checkbox.disabled = state.busy || !state.loaded || job.id == null; checkbox.checked = state.selectedJobs.has(String(job.id));
    checkbox.addEventListener("change", () => { if (!selectJob(job, checkbox.checked)) checkbox.checked = false; });
    selection.append(checkbox, document.createTextNode("加入对比"));
    const analyze = node("button", "button secondary job-analyze", "分析此岗位"); analyze.type = "button"; analyze.dataset.missingId = String(job.id == null); analyze.disabled = state.busy || !state.loaded || job.id == null;
    analyze.addEventListener("click", () => { if (selectJob(job, true)) focusAnalysis(`请重点分析「${job.title || "这份职位"}」与我已保存现状的匹配依据、风险和待确认问题。`); });
    bottom.append(analyze, selection);
    const result = node("div"); result.append(node("span", `verdict ${verdict}`, verdicts[verdict]));
    if (Number.isFinite(analysis.score)) result.append(node("span", "score", `排序分 ${Math.round(analysis.score)} · 非录用概率`));
    card.append(top, node("p", "job-company", [job.company || "公司待确认", job.city || "城市待确认", job.district].filter(Boolean).join(" · ")), badges, node("p", "job-facts", facts.slice(0, 4).join(" · ")), result, node("p", "job-summary", analysis.summary ? ruleExplanation(analysis.summary) : "条件以来源证据及实际沟通为准。"), details, bottom); return card;
  }
  function reconcileCards(area, jobs) {
    let cache = cardCaches.get(area); if (!cache) { cache = new Map(); cardCaches.set(area, cache); }
    const keep = new Set();
    let cursor = area.firstElementChild;
    jobs.forEach((job) => {
      const key = text(job.id ?? (job.source_url || `${job.source}:${job.company}:${job.title}`)), signature = JSON.stringify(job);
      let entry = cache.get(key);
      if (!entry || entry.signature !== signature) {
        const card = jobCard(job);
        if (entry?.card.querySelector("details")?.open) card.querySelector("details").open = true;
        if (entry?.card === cursor) cursor = cursor.nextElementSibling;
        entry?.card.remove(); entry = { card, signature }; cache.set(key, entry);
      }
      if (entry.card !== cursor) area.insertBefore(entry.card, cursor); else cursor = cursor.nextElementSibling;
      keep.add(key);
    });
    for (const [key, entry] of cache) { if (!keep.has(key)) { entry.card.remove(); cache.delete(key); } }
    area.querySelectorAll(":scope > .empty-state").forEach((el) => el.remove());
  }
  function renderJobs() {
    const existing = new Map([...state.previous, ...state.jobs].filter((j) => j.id != null).map((j) => [String(j.id), j]));
    for (const id of state.selectedJobs.keys()) { if (existing.has(id)) state.selectedJobs.set(id, existing.get(id)); else state.selectedJobs.delete(id); }
    renderSelection();
    const counts = { all: state.jobs.length, match: 0, needs_confirmation: 0, mismatch: 0 };
    state.jobs.forEach((j) => counts[verdicts[j.analysis?.verdict] ? j.analysis.verdict : "needs_confirmation"]++);
    document.querySelectorAll("[data-filter]").forEach((button) => { const active = button.dataset.filter === state.filter; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); button.querySelector("span").textContent = counts[button.dataset.filter]; });
    $("#results-total").textContent = `${state.taskId ? "本次采集" : "已保存"} · ${state.jobs.length} 份真实来源职位`;
    const area = $("#jobs-list");
    reconcileCards(area, state.jobs.filter((j) => state.filter === "all" || (verdicts[j.analysis?.verdict] ? j.analysis.verdict : "needs_confirmation") === state.filter));
    if (!area.childElementCount) { const empty = node("div", "empty-state"); empty.append(node("div", "empty-icon", "◎"), node("h3", "", state.taskActive ? "采集中，等待来源返回" : "当前没有可展示的职位"), node("p", "", "未返回职位不能证明没有招聘。请结合来源状态、信息完整度和检索条件判断。")); area.append(empty); }
  }
  function renderPrevious(jobs) { state.previous = realJobs(jobs); reconcileCards($("#previous-jobs"), state.previous); $("#previous-wrap").hidden = !state.previous.length; renderJobs(); }
  function showWarnings(value) { $("#search-warning").textContent = value; $("#search-warning").hidden = !value; }
  function acceptResults(data) {
    state.jobs = realJobs(data.jobs); renderJobs();
    $("#results-meta").textContent = `${data.stale ? "旧结果 · 画像或采集条件已变化，请重新检索" : "已保存结果 · 请核实招聘有效性"}${data.collected_at ? ` · ${timestamp(data.collected_at)}` : ""}`;
    showWarnings(warnings(data.warnings));
  }
  async function refreshJobs() {
    try { const data = await api("/api/jobs"); if (state.taskActive) { if (data.stale) renderPrevious(data.jobs); else { state.jobs = realJobs(data.jobs); renderJobs(); } } else acceptResults(data); }
    catch (error) { notify(`数据已保存，但职位刷新失败：${error.message} 请刷新页面读取结果。`, "error"); }
  }

  function renderModel(data, populate = true) {
    state.model = { base_url: text(data.base_url), model: text(data.model), has_key: data.has_key === true, configured: data.configured === true, key_storage: text(data.key_storage) };
    if (populate) { $("#model-base-url").value = state.model.base_url; $("#model-name").value = state.model.model; }
    $("#model-key").value = "";
    $("#model-storage").textContent = `${state.model.has_key ? "Key 已保存（不回显）" : "尚未保存 Key"} · 存储：${state.model.key_storage || "后端未报告"}`;
    $("#assistant-mode").textContent = state.model.configured ? `${state.model.model || "已配置模型"}` : "请先配置模型";
    $("#setup-model-state").textContent = state.model.configured ? `已配置 ${state.model.model}` : "填写服务地址、Key 和模型名称。";
    $("#header-model-state").textContent = state.model.configured ? `${state.model.model || "模型"} · 已配置` : "模型未配置";
    $("#header-model-state").classList.toggle("ready", state.model.configured);
    updateFirstTry();
    if (!state.model.configured) { $("#setup-guide").open = true; $("#setup-summary").textContent = "AI 功能需配置模型"; }
    else $("#setup-summary").textContent = "按需展开";
  }
  async function modelAction(button, action) {
    const buttons = $("#model-form").querySelectorAll("button"); buttons.forEach((b) => { b.disabled = true; });
    try { await action(); } catch (error) { $("#model-status").textContent = error.message; }
    finally { buttons.forEach((b) => { b.disabled = false; }); $("#model-key").value = ""; }
  }
  function selectedSources() { return Array.from(document.querySelectorAll("input[name=sources]:checked")).map((input) => input.value); }
  function searchPayload() { const sources = selectedSources(); if (!sources.length) throw new Error("请至少选择一个可用的真实来源。"); return { sources, keyword: $("#keyword").value.trim() }; }
  function renderSources(data) {
    sourcesLoaded = true;
    const selected = selectedSources();
    state.sources = list(data.sources).filter((s) => s && typeof s.id === "string" && !["demo", "import"].includes(s.id));
    const area = $("#source-options"); area.replaceChildren(node("legend", "", "来源 · 可多选"));
    const unavailableArea = $("#unavailable-options"); unavailableArea.replaceChildren();
    const blocked = ["unavailable", "unsupported", "unauthenticated", "restricted", "cdp_error", "schema_error"];
    const usable = state.sources.filter((s) => !blocked.includes(s.status));
    state.sources.forEach((source) => {
      const label = node("label"), input = node("input"); input.type = "checkbox"; input.name = "sources"; input.value = source.id;
      const unavailable = blocked.includes(source.status);
      input.dataset.unavailable = String(unavailable); input.disabled = unavailable;
      input.checked = !unavailable && (selected.length ? selected.includes(source.id) : source.id === (usable.find((s) => s.id === "boss") || usable[0])?.id);
      if (source.name) sourceNames[source.id] = text(source.name);
      const body = node("span", "", source.id === "ncss" ? "国家大学生就业平台" : source.name || sourceNames[source.id] || source.id);
      body.append(node("small", "", `${labelStatus(source.status)}${source.id === "ncss" ? " · 校招为主" : ""}`));
      label.title = `${text(source.description)}\n来源能力：${list(source.capabilities).join(" / ") || "未报告"}`;
      if (unavailable && source.description) body.append(node("small", "", source.description));
      label.append(input, body); (unavailable ? unavailableArea : area).append(label);
    });
    $("#unavailable-sources").hidden = !unavailableArea.childElementCount;
    $("#source-state").textContent = usable.length ? "BOSS 需本人登录。" : "暂无可用来源，请检查连接。";
    if (!usable.length) { $("#setup-guide").open = true; $("#setup-summary").textContent = "请检查连接与登录"; }
    const boss = state.sources.find((s) => s.id === "boss");
    $("#setup-browser-state").textContent = boss && ["available", "ready", "connected", "browser_connected"].includes(boss.status) ? "已连接；请确认专用窗口已登录。" : "打开专用窗口，由本人登录 BOSS。";
    syncControls();
  }
  function renderPlan(plan) {
    const area = $("#plan-content"); area.replaceChildren();
    const ids = Array.isArray(plan?.sources) ? plan.sources.map((s) => typeof s === "string" ? s : s?.id || s?.source).filter(Boolean) : selectedSources();
    const cities = list(state.profile.cities).join("、") || state.profile.city || "不限城市";
    const roles = text(plan?.keyword) || list(state.profile.target_roles).join("、") || "已保存的岗位条件";
    $("#plan-summary").textContent = `${cities} · ${roles} · ${ids.map((id) => sourceNames[id] || id).join("、") || "所选来源"}。其余条件取回后核对。`;
    if (warnings(plan?.warnings)) area.append(node("p", "notice", warnings(plan.warnings)));
    area.append(node("pre", "plan-value", JSON.stringify(plan, null, 2)));
    $("#plan-wrap").hidden = false;
  }
  function persistTask(id) { try { if (id) localStorage.setItem(taskStorage, id); else localStorage.removeItem(taskStorage); } catch { /* Progress still works when browser storage is disabled. */ } }
  function stopPolling() { clearTimeout(state.timer); state.timer = null; state.pollToken++; }
  const activeTask = (status) => ["pending", "queued", "running", "cancelling", "collecting", "in_progress"].includes(status);
  function renderTask(data) {
    const wasRunning = state.taskActive;
    const running = activeTask(data.status); state.taskActive = running;
    $("#task-panel").hidden = false;
    $("#task-status").textContent = `${labelStatus(data.status)} · ${stages[data.stage] || "等待来源报告进度"}`;
    const progress = $("#task-progress");
    const amount = typeof data.progress === "number" ? data.progress : data.progress?.percent;
    if (Number.isFinite(amount)) progress.value = Math.max(0, Math.min(100, amount)); else progress.removeAttribute("value");
    const area = $("#task-sources"); area.replaceChildren();
    const sources = Array.isArray(data.sources) ? data.sources : Object.entries(data.sources || {}).map(([id, value]) => ({ id, ...value }));
    sources.forEach((s) => {
      const row = node("div", "task-source");
      row.append(node("strong", "", s.name || sourceNames[s.id || s.source] || text(s.id || s.source)), node("span", "", `${labelStatus(s.status)} · ${stages[s.stage] || "尚未报告阶段"}`));
      if (s.progress != null) row.append(node("small", "", `进度：${describe(s.progress)}`));
      const counts = [["list_count", "列表职位"], ["detail_count", "已取详情"], ["jobs_count", "已收到职位"], ["full_jd_count", "完整 JD"], ["list_only_count", "仅列表"]].filter(([key]) => Number.isFinite(s[key])).map(([key, title]) => `${title} ${s[key]}`);
      if (counts.length) row.append(node("small", "", counts.join(" · ")));
      if (s.message || s.description) row.append(node("small", "", s.message || s.description));
      if (warnings(s.warnings)) row.append(node("small", "", warnings(s.warnings)));
      area.append(row);
    });
    const incoming = realJobs(data.jobs);
    if (!equal(incoming, state.jobs) || wasRunning !== running) { state.jobs = incoming; renderJobs(); }
    const full = state.jobs.filter((j) => j.completeness === "full_jd").length, lists = state.jobs.filter((j) => j.completeness === "list_only").length;
    $("#results-meta").textContent = `${running ? "本次采集中 · 已收到的部分结果" : `本次采集 · ${labelStatus(data.status)}`} · 完整 JD ${full} · 仅列表 ${lists} · 其余为摘要或用户提供`;
    showWarnings([warnings(data.warnings), !running && !["success", "completed", "empty"].includes(data.status) ? "本次存在失败、取消或部分完成状态；已收到的真实结果予以保留，不能据此判定没有招聘。" : ""].filter(Boolean).join("\n"));
    $("#cancel-task").hidden = !running; $("#cancel-task").disabled = data.status === "cancelling"; $("#resume-task").hidden = true; syncControls();
    return running;
  }
  async function pollTask(id, token) {
    try {
      const data = await api(`/api/tasks/${encodeURIComponent(id)}`);
      if (token !== state.pollToken || id !== state.taskId || state.deleting) return;
      const running = renderTask(data); if (data.plan) renderPlanIfClosed(data.plan);
      if (running) { if (!document.hidden) state.timer = setTimeout(() => pollTask(id, token), 1000); }
      else { persistTask(null); state.timer = null; }
    } catch (error) {
      if (token !== state.pollToken || state.deleting) return;
      $("#task-status").textContent = `进度读取失败：${error.message} 已显示结果仍保留。`;
      $("#resume-task").hidden = false;
      if (error.status === 404 || error.status === 409) { state.taskActive = false; persistTask(null); $("#cancel-task").hidden = true; $("#resume-task").hidden = true; syncControls(); }
    }
  }
  function renderPlanIfClosed(plan) { if ($("#plan-wrap").hidden) renderPlan(plan); }
  function beginTask(id, plan) {
    stopPolling(); state.taskId = id; state.taskActive = true; state.jobs = []; renderJobs(); persistTask(id);
    $("#task-panel").hidden = false; $("#task-status").textContent = "任务已创建 · 等待采集进度"; $("#task-progress").removeAttribute("value");
    $("#task-sources").replaceChildren(); $("#cancel-task").hidden = false; $("#cancel-task").disabled = false; $("#resume-task").hidden = true; showWarnings("");
    if (plan) renderPlan(plan); syncControls(); pollTask(id, state.pollToken);
  }

  async function createApplication(job, button) {
    await operation(button, "准备中…", async () => {
      await saveDirty(); const data = await api(`/api/jobs/${encodeURIComponent(job.id)}/application`, { method: "POST" });
      const area = $("#application-content"); area.replaceChildren();
      area.append(node("p", "muted", `${job.company || ""} · ${job.title || ""}`), node("p", "notice", "这是沟通草稿，没有投递，也没有发送任何消息。请核实后自行联系。"));
      if (data.notice) area.append(node("p", "field-help", data.notice));
      const draft = text(data.draft || data.message, "服务未返回草稿。"); const copy = node("button", "button secondary", "复制草稿"); copy.type = "button";
      copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(draft); copy.textContent = "已复制"; } catch { copy.textContent = "请选中文字复制"; } });
      area.append(node("div", "draft-box", draft), copy); detailList(area, "沟通前先确认", data.questions);
      area.append(node("h3", "", "来源提供的公开联系方式"));
      const contacts = list(data.contacts).filter((c) => c && typeof c.value === "string" && c.value.trim());
      if (!contacts.length) area.append(node("p", "field-help", "来源未提供公开联系方式，请通过原始页面联系。"));
      contacts.forEach((c) => area.append(node("p", "contact-value", `${text(c.label || c.kind, "公开联系")}：${c.value}`)));
      area.append(link(data.source_url || job.source_url, "打开原始职位 ↗")); showDialog("#application-dialog");
    });
  }

  function intakeHasDraft() {
    return $("#profile-intake-text").value.length > 0 || state.dirty || Object.keys(state.intakeConfirmedPatch).length > 0 || !!state.pendingChat || !!state.intakeController || state.busy;
  }
  function focusIntake() {
    const input = $("#profile-intake-text");
    input.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" }); input.focus({ preventScroll: true });
  }
  function updateFirstTry() {
    $("#first-try-model-state").textContent = Object.hasOwn(state.model, "configured") ? state.model.configured ? `${state.model.model || "模型"} 已配置，无需重填。` : "AI 功能需先配置自己的模型。" : "读取模型配置…";
    $("#first-try-example").textContent = intakeHasDraft() ? "继续写草稿" : "试用示例";
  }
  function setupFirstTry() {
    const storageKey = "fanwan-radar.first-try.v1", dialog = $("#first-try-dialog");
    let seen = false, autoAttempted = false, userActive = false, returnFocus = null, nextAction = null;
    try { seen = localStorage.getItem(storageKey) === "1"; } catch { /* The guide still works without persistent browser storage. */ }
    const open = () => {
      if (document.querySelector("dialog[open]")) return;
      returnFocus = document.activeElement; nextAction = null; updateFirstTry(); dialog.showModal();
    };
    const close = (action = null) => { nextAction = action; dialog.close(); };
    dialog.addEventListener("close", () => {
      seen = true; try { localStorage.setItem(storageKey, "1"); } catch { /* Remember dismissal for this page even when storage is blocked. */ }
      const action = nextAction; nextAction = null;
      if (action) action(); else if (returnFocus instanceof HTMLElement && returnFocus.isConnected && returnFocus !== document.body) returnFocus.focus({ preventScroll: true });
      else $("#open-first-try").focus({ preventScroll: true });
    });
    $("#open-first-try").addEventListener("click", open);
    $("#first-try-close").addEventListener("click", () => close());
    $("#first-try-start").addEventListener("click", () => close(focusIntake));
    $("#first-try-model").addEventListener("click", () => close(() => $("#open-model").click()));
    $("#first-try-connections").addEventListener("click", () => close(() => { $("#setup-guide").open = true; $("#setup-guide summary").focus(); }));
    $("#first-try-example").addEventListener("click", () => close(() => {
      if (intakeHasDraft()) { $("#intake-status").textContent = "草稿已保留，请继续填写。"; focusIntake(); }
      else { $("#apply-example").click(); focusIntake(); }
    }));
    const active = () => { userActive = true; };
    document.addEventListener("pointerdown", active, { capture: true, once: true });
    document.addEventListener("keydown", active, { capture: true, once: true });
    document.addEventListener("input", active, { capture: true, once: true });
    const autoOpen = () => {
      if (seen || autoAttempted || document.hidden) return;
      autoAttempted = true;
      const typing = document.activeElement?.matches("input, textarea, select, [contenteditable='true']");
      if (userActive || typing || state.busy || state.chatController || state.intakeController || document.querySelector("dialog[open]")) return;
      open();
    };
    setTimeout(autoOpen, 350);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) autoOpen(); });
  }

  let reloadApproved = false;
  function hasUnsavedWork() {
    return intakeHasDraft() || !!state.chatController || $("#chat-input").value.trim().length > 0 || !!document.querySelector("#model-dialog[open], #import-dialog[open]");
  }
  function setupUpdateCheck() {
    const currentBuild = text(document.body.dataset.appBuild);
    if (!currentBuild || currentBuild.includes("__BUILD_ID__")) return;
    let timer = null, controller = null, lastCheck = 0;
    const schedule = () => { clearTimeout(timer); if (!document.hidden) timer = setTimeout(check, Math.max(1000, 60000 - (Date.now() - lastCheck))); };
    async function check() {
      if (document.hidden || controller) return;
      lastCheck = Date.now(); controller = new AbortController();
      const request = controller, timeout = setTimeout(() => request.abort(), 10000);
      try {
        const response = await fetch("/api/health", { signal: request.signal, cache: "no-store", headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const data = await response.json(), build = text(data.build_id);
        if (request.signal.aborted || document.hidden || !build) return;
        $("#update-banner").hidden = build === currentBuild;
      } catch { /* An optional update check never interrupts normal work. */ }
      finally { clearTimeout(timeout); if (controller === request) controller = null; schedule(); }
    }
    $("#reload-update").addEventListener("click", () => {
      const message = hasUnsavedWork() ? "刷新会丢失未保存的描述、草稿或未完成的回复。请确认已处理好需要保留的内容。现在刷新到新版本？" : "现在刷新页面，使用新版本？";
      if (!window.confirm(message)) return;
      reloadApproved = true; window.location.reload(); setTimeout(() => { reloadApproved = false; }, 2000);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { clearTimeout(timer); controller?.abort(); }
      else if (Date.now() - lastCheck >= 60000) check(); else schedule();
    });
    lastCheck = Date.now(); schedule();
  }

  buildProfile();
  $("#profile-editor").open = false;
  form.addEventListener("input", markDirty);
  form.addEventListener("invalid", (event) => { let parent = event.target.parentElement; while (parent && parent !== form.parentElement) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; } $("#profile-editor").open = true; }, true);
  $("#profile-intake-form").addEventListener("submit", (event) => { event.preventDefault(); interpretProfile(); });
  $("#profile-intake-text").addEventListener("input", () => {
    $("#intake-length").textContent = `${$("#profile-intake-text").value.length} / 20000 字`;
    invalidateIntake("描述原文已修改。原预览仍保留，请按新原文重新整理后再填入。");
  });
  $("#cancel-interpret").addEventListener("click", () => { state.intakeController?.abort(); });
  $("#apply-intake").addEventListener("click", applyIntakePreview);
  $("#discard-intake").addEventListener("click", () => {
    state.intakeController?.abort(); state.intakePreview = null;
    $("#intake-preview").hidden = true; $("#intake-changes").replaceChildren(); $("#intake-questions").replaceChildren();
    $("#intake-status").textContent = "预览已收起，草稿保留。"; syncControls();
  });
  $("#apply-example").addEventListener("click", () => {
    if (state.busy || state.intakeController) return;
    const input = $("#profile-intake-text");
    if (input.value.trim() && !window.confirm("用示例替换当前描述？只替换文字，不会改动条件表单。")) return;
    input.value = "郑州客服，月薪6000起，每周休两天，五险一金，不接受夜班。";
    input.dispatchEvent(new Event("input", { bubbles: true })); input.focus();
    $("#intake-status").textContent = "示例已填，可修改后整理。尚未保存。";
  });
  $("#open-setup").addEventListener("click", () => { const guide = $("#setup-guide"); guide.open = !guide.open; if (guide.open) guide.querySelector("summary").focus(); });
  form.addEventListener("submit", (event) => { event.preventDefault(); operation($("#save-profile"), "保存中…", async () => { await saveDirty(true); notify(list(state.profile.confirmation_required).length ? "本次条件已保存，其他历史默认项仍待确认，不作为硬条件。" : "条件已保存，岗位未知信息仍需向招聘方核实。"); }, $("#profile-status")); });
  $("#reload-profile").addEventListener("click", () => operation($("#reload-profile"), "读取中…", async () => {
    const current = await api("/api/profile"); state.current = current;
    const area = $("#conflict-preview"); area.replaceChildren(node("p", "field-help", `当前版本 ${current.revision} · ${timestamp(current.updated_at)}。本页表单未改变。`));
    const details = node("details"); details.append(node("summary", "", "查看当前已保存画像"));
    const preview = node("pre", "preview", describe(current.profile)); details.append(preview); area.append(details); $("#use-current-profile").hidden = false;
  }));
  $("#use-current-profile").addEventListener("click", async () => { if (state.current && window.confirm("放弃本页尚未保存的编辑，使用刚读取的当前画像？")) { const current = state.current; applySnapshot(current); if (!state.pendingChat) renderMessages(current.messages); await refreshJobs(); } });
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault(); const input = $("#chat-input"), message = input.value.trim(); if (!message) return;
    if (state.busy || !state.loaded) return;
    if (state.pendingChat) { $("#chat-status").textContent = "请先重试或结束本次尝试。重试保留原始问题、职位与请求编号。"; return; }
    if (state.dirty || state.conflict) { $("#chat-status").textContent = "请先保存或处理画像版本冲突，再分析已保存的现状。分析不会自动保存或修改你的条件。"; return; }
    state.pendingChat = { message, request_id: globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`, ...meta(), job_ids: Array.from(state.selectedJobs.values(), (job) => job.id) };
    runAnalysis();
  });
  $("#stop-chat").addEventListener("click", () => { state.chatController?.abort(); });
  $("#retry-chat").addEventListener("click", () => { if (state.pendingChat && !state.busy) runAnalysis(); });
  $("#discard-chat").addEventListener("click", () => {
    if (state.busy || state.chatController) return;
    state.chatDraft?.user.el.remove(); state.chatDraft?.assistant.el.remove(); state.pendingChat = null; state.chatDraft = null;
    $("#chat-status").textContent = "本次尝试已结束。若中断时后台已完成，刷新页面可确认已保存记录。"; syncControls(); $("#chat-input").focus();
  });
  $("#clear-selection").addEventListener("click", () => { state.selectedJobs.clear(); renderSelection(); });
  $("#analyze-current").addEventListener("click", () => focusAnalysis("请结合我已保存的现状与已有真实职位，分析优先方向、取舍、证据缺口和下一步应确认的问题。"));
  $("#compare-jobs").addEventListener("click", () => { if (state.selectedJobs.size >= 2) focusAnalysis("请比较所选职位与我已保存现状的匹配情况，分别列出有来源依据的优缺点、未知条件和建议确认的问题，再说明优先顺序及理由。"); });
  $("#chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!state.busy) $("#chat-form").requestSubmit(); } });
  document.querySelectorAll("#chat-suggestions button").forEach((button) => button.addEventListener("click", () => { $("#chat-input").value = button.textContent; $("#chat-input").focus(); }));
  $("#open-model").addEventListener("click", () => { showDialog("#model-dialog"); $("#model-status").textContent = ""; });
  $("#setup-model").addEventListener("click", () => showDialog("#model-dialog"));
  async function sourceAction(button, pending, action) {
    if (button.disabled) return;
    const previous = button.textContent; button.disabled = true; button.textContent = pending;
    try { await action(); } catch (error) { $("#setup-browser-state").textContent = error.message; }
    finally { button.disabled = false; button.textContent = previous; }
  }
  $("#open-boss-browser").addEventListener("click", () => sourceAction($("#open-boss-browser"), "打开中…", async () => {
    const data = await api("/api/browser/open", { method: "POST" }); notify(data.message || "请在独立浏览器窗口中本人登录。");
    renderSources(await api("/api/sources"));
  }));
  $("#refresh-sources").addEventListener("click", () => sourceAction($("#refresh-sources"), "检测中…", async () => { renderSources(await api("/api/sources")); $("#setup-browser-state").textContent += " 已刷新；登录状态以实际查询验证。"; }));
  $("#model-dialog").addEventListener("close", () => { $("#model-key").value = ""; });
  $("#model-form").addEventListener("submit", (event) => { event.preventDefault(); modelAction($("#save-model"), async () => {
    const body = { base_url: $("#model-base-url").value.trim(), model: $("#model-name").value.trim() }; if ($("#model-key").value.trim()) body.api_key = $("#model-key").value.trim();
    await api("/api/model-settings", { method: "PUT", body }); renderModel(await api("/api/model-settings")); $("#model-status").textContent = state.model.configured ? "设置已保存，可以测试连接。" : "Key 和地址已保护保存，请读取或填写模型名称后测试。";
  }); });
  $("#discover-models").addEventListener("click", () => modelAction($("#discover-models"), async () => {
    const body = { base_url: $("#model-base-url").value.trim(), model: $("#model-name").value.trim() };
    if ($("#model-key").value.trim()) body.api_key = $("#model-key").value.trim();
    $("#model-status").textContent = "正在向你指定的服务读取模型名称…";
    const data = await api("/api/model-settings/discover", { method: "POST", body });
    if (data.settings) renderModel(data.settings);
    const options = $("#model-options"); options.replaceChildren(); list(data.models).forEach((id) => { const option = node("option"); option.value = id; options.append(option); });
    $("#model-status").textContent = data.message || "请核对模型列表并选择要使用的名称。";
  }));
  $("#test-model").addEventListener("click", () => modelAction($("#test-model"), async () => { $("#model-status").textContent = "正在测试已保存的配置…"; const data = await api("/api/model-settings/test", { method: "POST" }); $("#model-status").textContent = `${data.ok ? "连接成功" : "连接未通过"} · ${text(data.message)}`; }));
  $("#remove-model-key").addEventListener("click", () => modelAction($("#remove-model-key"), async () => { if (!window.confirm("移除本机已保存的模型 Key？移除后聊天需要重新配置。")) return; await api("/api/model-settings/key", { method: "DELETE" }); renderModel(await api("/api/model-settings")); $("#model-status").textContent = "已移除 Key。"; }));
  $("#preview-plan").addEventListener("click", () => operation($("#preview-plan"), "生成计划中…", async () => { await saveDirty(); const data = await api("/api/search/plan", { method: "POST", body: searchPayload() }); renderPlan(data.plan); }));
  $("#search-form").addEventListener("submit", (event) => { event.preventDefault(); if (state.taskActive) return; operation($("#search-button"), "创建任务中…", async () => {
    await saveDirty(); const data = await api("/api/search", { method: "POST", body: { ...searchPayload(), ...meta() } });
    if (!data.task_id) throw new Error("服务未返回采集任务编号，请刷新检查状态。");
    renderPrevious(state.jobs.length ? state.jobs : state.previous); beginTask(String(data.task_id), data.plan);
  }); });
  $("#cancel-task").addEventListener("click", async () => {
    const id = state.taskId; if (!id) return; $("#cancel-task").disabled = true;
    try { await api(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }); if (id !== state.taskId || state.deleting) return; $("#task-status").textContent = "取消请求已提交 · 等待后台结束，已收到结果仍保留"; stopPolling(); pollTask(id, state.pollToken); }
    catch (error) { if (id === state.taskId) { notify(error.message, "error"); $("#cancel-task").disabled = false; } }
  });
  $("#resume-task").addEventListener("click", () => { if (!state.taskId) return; stopPolling(); $("#resume-task").hidden = true; pollTask(state.taskId, state.pollToken); });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; renderJobs(); }));
  document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#open-import").addEventListener("click", () => showDialog("#import-dialog"));
  let importRead = 0;
  $("#import-file").addEventListener("change", async () => { const file = $("#import-file").files[0], token = ++importRead; if (!file) return; $("#import-submit").disabled = true;
    try { const content = await file.text(); if (token === importRead) { $("#import-json").value = content; $("#import-status").textContent = `已读取 ${file.name}，请核对真实性后导入。`; } }
    catch { $("#import-status").textContent = "文件读取失败。"; } finally { if (token === importRead) $("#import-submit").disabled = state.busy || !state.loaded; }
  });
  $("#import-form").addEventListener("submit", (event) => { event.preventDefault(); operation($("#import-submit"), "导入中…", async () => {
    let payload; try { payload = JSON.parse($("#import-json").value); } catch { throw new Error("JSON 格式不正确，请检查括号和双引号。"); }
    if (!list(payload?.jobs).length || payload.jobs.some((j) => !j || typeof j !== "object" || Array.isArray(j))) throw new Error("请提供包含非空 jobs 数组的 JSON 对象。");
    if (payload.jobs.some((j) => j.is_demo || j.source === "demo")) throw new Error("只能导入你收集的真实职位。");
    if (state.taskActive) throw new Error("请等待或取消当前采集任务后再导入。");
    await saveDirty(); const data = await api("/api/jobs/import", { method: "POST", body: { jobs: payload.jobs.map((j) => ({ ...j, source: "import", completeness: "user_provided" })) } });
    state.taskId = null; acceptResults(data); $("#task-panel").hidden = true; renderPrevious([]); $("#import-dialog").close(); notify("真实 JD 已导入，标记为用户提供、未核验。");
  }, $("#import-status")); });
  $("#delete-data").addEventListener("click", () => showDialog("#delete-dialog"));
  $("#confirm-delete").addEventListener("click", () => operation($("#confirm-delete"), "清除中…", async () => {
    state.deleting = true; stopPolling(); state.intakeController?.abort();
    try {
      await api("/api/data", { method: "DELETE", headers: { "X-Confirm-Delete": "yes" } });
      state.taskId = null; state.taskActive = false; persistTask(null); state.pendingChat = null; state.chatDraft = null; state.selectedJobs.clear(); state.loaded = false;
      state.intakeController = null; state.intakePreview = null; state.intakeConfirmedPatch = {}; state.scheduleDraft = null;
      $("#profile-intake-form").reset(); $("#intake-length").textContent = "0 / 20000 字"; $("#intake-status").textContent = "";
      $("#intake-preview").hidden = true; $("#intake-changes").replaceChildren(); $("#intake-questions").replaceChildren(); $("#interpret-profile").textContent = "帮我整理要求";
      $("#task-panel").hidden = true; $("#plan-wrap").hidden = true; renderPrevious([]); acceptResults({ jobs: [] }); renderMessages([]);
      $("#chat-input").value = ""; $("#chat-status").textContent = ""; $("#keyword").value = ""; $("#import-form").reset(); importRead++; $("#import-status").textContent = ""; $("#application-content").replaceChildren(); $("#chat-suggestions").hidden = false;
      applySnapshot(await api("/api/profile")); $("#delete-dialog").close(); notify("求职数据已清除，模型配置保留。");
    } finally { state.deleting = false; if (state.taskActive && state.taskId) pollTask(state.taskId, state.pollToken); }
  }, $("#delete-status")));
  window.addEventListener("beforeunload", (event) => { if (!reloadApproved && hasUnsavedWork()) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopPolling(); else if (state.taskActive && state.taskId && !state.deleting) { stopPolling(); pollTask(state.taskId, state.pollToken); } });

  async function initialize() {
    syncControls(); renderMessages([]); renderJobs();
    let task; try { task = localStorage.getItem(taskStorage); } catch { task = null; }
    let jobsSettled = false, resumed = false;
    const resume = () => { if (task && state.loaded && jobsSettled && !resumed) { resumed = true; renderPrevious(state.jobs); beginTask(task); } };
    // Each response applies immediately. Slow source/browser checks never gate local editing.
    await Promise.allSettled([
      api("/api/profile").then((data) => { applySnapshot(data); renderMessages(data.messages); syncControls(); resume(); }).catch(() => { profileStatus("条件未能载入，已暂停修改以保护本地数据。请刷新重试。"); $("#profile-summary").textContent = "条件读取失败，请刷新重试"; }),
      api("/api/jobs").then((data) => { acceptResults(data); }).catch(() => { $("#results-meta").textContent = "保存的职位读取失败，请刷新重试；没有使用替代数据。"; }).finally(() => { jobsSettled = true; resume(); syncControls(); }),
      api("/api/model-settings").then((data) => { renderModel(data); syncControls(); }).catch(() => { $("#assistant-mode").textContent = "模型状态读取失败 · 可在设置中检查"; $("#header-model-state").textContent = "模型状态未读取"; }),
      api("/api/sources").then(renderSources).catch(() => { $("#source-state").textContent = "来源检测失败；可以继续编辑与分析，或在连接指南中刷新。"; $("#setup-guide").open = true; })
    ]);
  }
  initialize().catch((error) => { notify(error.message, "error"); syncControls(); });
  setupFirstTry();
  setupUpdateCheck();
})();
