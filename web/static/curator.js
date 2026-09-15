const STATUS_LABELS = {
  discuss: "нужно обсудить",
  remarks: "есть замечания",
  ok: "всё хорошо",
  empty: "не заполнено",
};
const LLM_LABELS = {
  none: "без LLM",
  fresh: "проанализировано",
  stale: "данные изменились",
  queued: "в очереди",
  running: "анализируется…",
  error: "ошибка анализа",
};
const SEVERITY_LABELS = { blocker: "Не заполнено", warning: "Обсудить", advice: "Совет" };

const state = { week: null, students: [], selected: null, pollTimer: null, lastDone: null };
const $ = (selector) => document.querySelector(selector);

class AuthError extends Error {}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && !path.endsWith("/login")) throw new AuthError(payload.detail || "Нужен вход куратора.");
  if (!response.ok) throw new Error(payload.detail || "Что-то пошло не так. Попробуй ещё раз.");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function setMessage(element, text = "", kind = "") {
  element.textContent = text;
  element.className = `form-message ${kind}`.trim();
}

function setLoading(button, loading) {
  button.disabled = loading;
  button.classList.toggle("loading", loading);
}

function parseIsoDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function isoDate(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
}

function mondayOf(date) {
  const result = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  result.setDate(result.getDate() - ((result.getDay() + 6) % 7));
  return result;
}

function compactDate(date) {
  return `${date.getDate()}.${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function makeWeekLabel(start) {
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  return `${compactDate(start)}-${compactDate(end)}`;
}

function setSelectedWeek(value) {
  const start = mondayOf(parseIsoDate(value));
  state.week = { start: isoDate(start), label: makeWeekLabel(start) };
  $("#week-date").value = state.week.start;
  $("#week-range").textContent = state.week.label;
  loadGroup();
}

function shiftWeek(days) {
  const start = parseIsoDate(state.week.start);
  start.setDate(start.getDate() + days);
  setSelectedWeek(isoDate(start));
}

function targetLabel(target) {
  if (target === "week") return "Неделя";
  if (target === "reflection") return "Рефлексия";
  const [kind, value] = String(target).split(":");
  if (kind === "goal") return `Цель ${value}`;
  if (kind === "task") return `Задача ${value}`;
  if (kind === "day") return value;
  return target;
}

function showLogin() {
  stopPolling();
  $("#curator").classList.add("hidden");
  $("#logout").classList.add("hidden");
  $("#login-card").classList.remove("hidden");
  $("#password").focus();
}

function showCurator() {
  $("#login-card").classList.add("hidden");
  $("#curator").classList.remove("hidden");
  $("#logout").classList.remove("hidden");
}

function handleError(error, element = $("#page-message")) {
  if (error instanceof AuthError) return showLogin();
  setMessage(element, error.message);
}

async function loadGroup() {
  try {
    const data = await api(`/api/curator/review?week=${encodeURIComponent(state.week.label)}`);
    showCurator();
    setMessage($("#page-message"));
    state.students = data.students;
    renderList();
    updateQueue(data.queue);
    if (state.selected && state.students.some((student) => student.student === state.selected)) {
      loadStudent(state.selected);
    } else {
      state.selected = null;
      $("#student-report").innerHTML = '<p class="muted">Выбери студента слева.</p>';
    }
  } catch (error) {
    handleError(error);
  }
}

function renderList() {
  const list = $("#student-list");
  if (!state.students.length) {
    list.innerHTML = '<p class="muted">В таблице нет вкладок студентов.</p>';
    return;
  }
  list.innerHTML = state.students.map((student) => {
    const counts = student.status === "empty" ? "" : ` · ${student.warnings} обсудить, ${student.advice} советов`;
    return `
      <button class="student-row ${student.student === state.selected ? "selected" : ""}" type="button" data-student="${escapeHtml(student.student)}">
        <span class="status-dot status-${student.status}" title="${STATUS_LABELS[student.status]}"></span>
        <span class="student-name">${escapeHtml(student.student)}</span>
        <small>${STATUS_LABELS[student.status]}${counts}${student.status === "empty" ? "" : ` · ${LLM_LABELS[student.llm_state] || ""}`}</small>
      </button>`;
  }).join("");
}

async function loadStudent(name) {
  state.selected = name;
  renderList();
  try {
    const report = await api(`/api/curator/review/student?name=${encodeURIComponent(name)}&week=${encodeURIComponent(state.week.label)}`);
    if (state.selected === name) renderReport(report);
  } catch (error) {
    handleError(error);
  }
}

function renderFindings(findings, filter) {
  const items = findings.filter(filter);
  if (!items.length) return "";
  return items.map((finding) => `
    <li class="finding severity-${finding.severity}">
      <b>${escapeHtml(targetLabel(finding.target))}</b>${escapeHtml(finding.message)}
      <small>${SEVERITY_LABELS[finding.severity]} · ${finding.source === "llm" ? "LLM" : "правило"}</small>
    </li>`).join("");
}

function section(title, body) {
  return body ? `<div class="report-section"><h3>${title}</h3>${body}</div>` : "";
}

function renderReport(report) {
  const plan = report.plan;
  const goals = plan.goals.length
    ? `<ol class="plan-goals">${plan.goals.map((goal, index) => `<li><b>${index + 1}.</b> ${escapeHtml(goal)}</li>`).join("")}</ol>`
    : "";
  const tasks = plan.tasks.length
    ? `<ul class="plan-tasks">${plan.tasks.map((task) => `<li><b>${task.index}. ${escapeHtml(task.day || "—")} · ${escapeHtml(task.sphere || "—")}</b> ${escapeHtml(task.text || "—")} ${escapeHtml(task.status)}</li>`).join("")}</ul>`
    : "";
  const findings = report.findings;
  const findingsHtml = [
    ["Неделя", (f) => f.target === "week" || f.target.startsWith("day:")],
    ["Цели", (f) => f.target.startsWith("goal:")],
    ["Задачи", (f) => f.target.startsWith("task:")],
    ["Рефлексия", (f) => f.target === "reflection"],
  ].map(([title, filter]) => {
    const items = renderFindings(findings, filter);
    return items ? section(title, `<ul class="findings">${items}</ul>`) : "";
  }).join("");
  const rewrites = report.rewrites.length
    ? `<ul class="rewrites">${report.rewrites.map((rewrite) => `
        <li class="rewrite"><b>${escapeHtml(targetLabel(rewrite.target))}:</b> <s>${escapeHtml(rewrite.original)}</s><span>→ ${escapeHtml(rewrite.suggestion)}</span></li>`).join("")}</ul>`
    : "";
  const questions = report.questions.length
    ? `<ul class="questions">${report.questions.map((question) => `<li>${escapeHtml(question)}</li>`).join("")}</ul>`
    : "";
  const meta = report.analyzed_at
    ? `${LLM_LABELS[report.llm_state]} · ${new Date(report.analyzed_at).toLocaleString("ru-RU")}${report.model ? ` · ${escapeHtml(report.model)}` : ""}`
    : LLM_LABELS[report.llm_state];
  const busy = report.llm_state === "queued" || report.llm_state === "running";

  $("#student-report").innerHTML = `
    <div class="report-head">
      <div>
        <h2>${escapeHtml(report.student)}</h2>
        <span class="status-badge"><span class="status-dot status-${report.status}"></span>${STATUS_LABELS[report.status]}</span>
        <div class="report-meta">${report.status === "empty" ? "" : meta}</div>
      </div>
    </div>
    ${report.summary ? `<p class="report-summary">${escapeHtml(report.summary)}</p>` : ""}
    ${report.error ? `<p class="report-error">Анализ не удался: ${escapeHtml(report.error)}</p>` : ""}
    ${report.llm_state === "stale" ? '<p class="report-error">Данные в таблице изменились после анализа — результат LLM может быть неактуален.</p>' : ""}
    ${findingsHtml || section("Замечания", '<p class="muted">Замечаний нет.</p>')}
    ${section("Как можно переформулировать", rewrites)}
    ${section("Вопросы для встречи", questions)}
    ${section("Цели недели", goals)}
    ${section("Задачи", tasks)}
    ${report.status === "empty" ? "" : `
      <div class="report-actions">
        <button class="primary-button" id="analyze-student" type="button" ${busy ? "disabled" : ""}>
          <span>${report.llm_state === "none" ? "Проанализировать" : "Проанализировать заново"}</span><b>↻</b>
        </button>
      </div>`}`;
  const button = $("#analyze-student");
  if (button) button.addEventListener("click", () => analyze(report.student, button));
}

async function analyze(student, button) {
  setLoading(button, true);
  try {
    const body = { week_label: state.week.label, ...(student ? { student } : {}) };
    const result = await api("/api/curator/analyze", { method: "POST", body: JSON.stringify(body) });
    if (!student && result.added === 0) setMessage($("#page-message"), "Все заполненные планы уже проанализированы.", "success");
    updateQueue(result.queue);
    await loadGroup();
  } catch (error) {
    handleError(error);
  } finally {
    setLoading(button, false);
  }
}

function updateQueue(progress) {
  const bar = $("#queue-bar");
  if (!progress || !progress.busy) {
    bar.classList.add("hidden");
    if (state.pollTimer) {
      stopPolling();
      loadGroup();
    }
    return;
  }
  bar.classList.remove("hidden");
  const percent = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;
  $("#queue-fill").style.width = `${percent}%`;
  const failed = progress.failed ? `, ошибок: ${progress.failed}` : "";
  const current = progress.current ? ` · сейчас: ${progress.current}` : "";
  $("#queue-text").textContent = `Проанализировано ${progress.done} из ${progress.total}${failed}${current}`;
  if (state.lastDone !== null && state.lastDone !== progress.done) refreshAfterProgress();
  state.lastDone = progress.done;
  if (!state.pollTimer) state.pollTimer = setInterval(pollQueue, 3000);
}

async function refreshAfterProgress() {
  try {
    const data = await api(`/api/curator/review?week=${encodeURIComponent(state.week.label)}`);
    state.students = data.students;
    renderList();
    if (state.selected) loadStudent(state.selected);
  } catch (error) {
    handleError(error);
  }
}

async function pollQueue() {
  try {
    updateQueue(await api("/api/curator/queue"));
  } catch (error) {
    handleError(error);
  }
}

function stopPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = null;
  state.lastDone = null;
}

async function login(event) {
  event.preventDefault();
  const button = $("#login-form button");
  setLoading(button, true);
  try {
    await api("/api/curator/login", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    $("#password").value = "";
    setMessage($("#login-message"));
    await loadGroup();
  } catch (error) {
    setMessage($("#login-message"), error.message);
  } finally {
    setLoading(button, false);
  }
}

async function logout() {
  await api("/api/curator/logout", { method: "POST" }).catch(() => {});
  showLogin();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#login-form").addEventListener("submit", login);
  $("#logout").addEventListener("click", logout);
  $("#week-date").addEventListener("change", (event) => setSelectedWeek(event.target.value));
  $("#previous-week").addEventListener("click", () => shiftWeek(-7));
  $("#next-week").addEventListener("click", () => shiftWeek(7));
  $("#analyze-group").addEventListener("click", (event) => analyze(null, event.currentTarget));
  $("#student-list").addEventListener("click", (event) => {
    const row = event.target.closest("[data-student]");
    if (row) loadStudent(row.dataset.student);
  });
  setSelectedWeek(isoDate(new Date()));
});
