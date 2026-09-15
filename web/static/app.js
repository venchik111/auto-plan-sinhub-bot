const state = {
  user: null,
  config: null,
  draft: null,
  week: null,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Что-то пошло не так. Попробуй ещё раз.");
  }
  return payload;
}

function setMessage(element, text = "", kind = "") {
  element.textContent = text;
  element.className = `form-message ${kind}`.trim();
}

function setLoading(button, loading, label) {
  button.disabled = loading;
  button.classList.toggle("loading", loading);
  if (label) button.querySelector("span").textContent = loading ? "Подожди…" : label;
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
  const daysSinceMonday = (result.getDay() + 6) % 7;
  result.setDate(result.getDate() - daysSinceMonday);
  return result;
}

function compactDate(date) {
  return `${date.getDate()}.${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function makeWeekLabel(start) {
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6);
  return `${compactDate(start)}-${compactDate(end)}`;
}

function setSelectedWeek(value, shouldLoad = true) {
  const start = mondayOf(parseIsoDate(value));
  state.week = { start: isoDate(start), label: makeWeekLabel(start) };
  $("#week-date").value = state.week.start;
  $("#week-range").textContent = `${state.week.label} · можно выбрать любую неделю`;
  if (shouldLoad) {
    if (state.draft && state.draft.week_label !== state.week.label) {
      startOver();
      setMessage($("#plan-message"), "Неделя изменена — старый черновик сброшен.", "success");
    }
    loadSchedule();
  }
}

function populateWeeks() {
  setSelectedWeek(state.config.current_week_start, false);
}

function showUser(user) {
  state.user = user;
  $("#connected-name").textContent = user.display_name;
  $("#auth-guest").classList.add("hidden");
  $("#connected-state").classList.remove("hidden");
  const savedAnswers = user.profile?.guiding_answers || {};
  document.querySelectorAll("[data-guiding-answer]").forEach((input) => {
    input.value = savedAnswers[input.dataset.guidingAnswer] || "";
  });
  $("#generate-button").disabled = !hasPlanningDetails($("#source-text").value, collectGuidingAnswers());
  document.querySelectorAll(".step")[0].classList.remove("active");
  document.querySelectorAll(".step")[1].classList.add("active");
}

function showRegistration() {
  $("#login-form").classList.add("hidden");
  $("#account-register-form").classList.remove("hidden");
  $("#register-login").focus();
}

function showLogin() {
  $("#account-register-form").classList.add("hidden");
  $("#login-form").classList.remove("hidden");
  $("#login").focus();
}

function selectOptions(values, selected) {
  return values.map((value) => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function taskDuration(task) {
  return Number(task?.time_minutes) || 0;
}

function taskText(task) {
  return String(task?.text ?? task?.task ?? "").trim();
}

function clientWarnings(tasks = []) {
  const warnings = [];
  const loads = {};
  const seen = new Set();
  (Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const day = String(task?.day ?? "");
    const text = taskText(task);
    loads[day] = (loads[day] || 0) + taskDuration(task);
    const signature = `${day}|${text.toLocaleLowerCase()}`;
    if (text && seen.has(signature)) {
      warnings.push(`Конфликт: задача "${text}" дублируется на ${day}.`);
    }
    seen.add(signature);
  });
  Object.entries(loads).forEach(([day, load]) => {
    if (load > 180) warnings.push(`Конфликт нагрузки: на ${day} запланировано ${load} минут задач. Лучше распределить их равномернее.`);
  });
  return warnings;
}

function taskRowHtml(task, index) {
  const text = taskText(task);
  const sphere = task?.sphere || state.config.spheres[0];
  const time = task?.time_minutes ?? "";
  return `
    <div class="task-row" data-task-index="${index}" draggable="true">
      <span class="drag-handle" title="Перетащи задачу в другой день" aria-label="Перетащить задачу">⠿</span>
      <select data-task-sphere aria-label="Сфера">${selectOptions(state.config.spheres, sphere)}</select>
      <input data-task-text value="${escapeHtml(text)}" maxlength="300" aria-label="Задача">
      <input data-task-time type="number" min="1" max="1440" placeholder="мин" value="${escapeHtml(time)}" aria-label="Минуты">
      <button class="delete-button" type="button" data-delete-task="${index}" aria-label="Удалить задачу">×</button>
    </div>
  `;
}

function renderDraft(draft) {
  const tasks = (Array.isArray(draft?.tasks) ? draft.tasks : []).map((task) => ({
    ...task,
    text: taskText(task),
    sphere: task?.sphere || state.config.spheres[0],
    day: state.config.days.includes(task?.day) ? task.day : state.config.days[0],
  }));
  const goals = (Array.isArray(draft?.goals) ? draft.goals : [])
    .map((goal) => String(goal ?? "").trim())
    .filter(Boolean);
  const warnings = [...new Set([...(Array.isArray(draft?.warnings) ? draft.warnings : []), ...clientWarnings(tasks)])];
  state.draft = { ...draft, goals, tasks, warnings };
  $("#review-card").classList.remove("hidden");
  $("#success-card").classList.add("hidden");
  $("#reflection-card").classList.add("hidden");
  $("#goals-list").innerHTML = goals.map((goal, index) => `
    <div class="goal-row">
      <span class="goal-number">${index + 1}</span>
      <input data-goal value="${escapeHtml(goal)}" maxlength="300" aria-label="Цель ${index + 1}">
      <button class="delete-button" type="button" data-delete-goal="${index}" aria-label="Удалить цель">×</button>
    </div>
  `).join("");

  $("#task-list").innerHTML = state.config.days.map((day) => {
    const dayTasks = tasks
      .map((task, index) => ({ task, index }))
      .filter(({ task }) => task.day === day);
    return `
      <section class="day-column" data-day-column="${day}">
        <div class="day-column-head"><strong>${day}</strong><span data-day-load="${day}">0 мин</span></div>
        <div class="day-dropzone" data-day-drop="${day}">
          ${dayTasks.map(({ task, index }) => taskRowHtml(task, index)).join("") || '<span class="empty-day">Перетащи задачу сюда</span>'}
        </div>
      </section>
    `;
  }).join("");
  updateDayLoads();

  const warningsBox = $("#warnings-box");
  $("#warnings-list").innerHTML = warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  warningsBox.classList.toggle("hidden", !warnings.length);
  document.querySelectorAll(".step")[1].classList.remove("active");
  document.querySelectorAll(".step")[2].classList.add("active");
  $("#review-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function updateDayLoads() {
  document.querySelectorAll("[data-day-drop]").forEach((zone) => {
    const load = [...zone.querySelectorAll("[data-task-time]")]
      .reduce((total, input) => total + (Number(input.value) || 0), 0);
    const badge = document.querySelector(`[data-day-load="${zone.dataset.dayDrop}"]`);
    if (badge) {
      badge.textContent = `${load} мин`;
      badge.classList.toggle("overloaded", load > 180);
    }
  });
}

function refreshTaskWarnings() {
  const current = collectDraft();
  state.draft = current;
  const warningsBox = $("#warnings-box");
  $("#warnings-list").innerHTML = current.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  warningsBox.classList.toggle("hidden", !current.warnings.length);
}

function collectDraft() {
  const goals = [...document.querySelectorAll("[data-goal]")]
    .map((input) => String(input?.value ?? "").trim())
    .filter(Boolean);
  const tasks = [...document.querySelectorAll("[data-task-index]")].map((row) => {
    const sphereInput = row.querySelector("[data-task-sphere]");
    const textInput = row.querySelector("[data-task-text]");
    const timeInput = row.querySelector("[data-task-time]");
    const dayZone = row.closest("[data-day-drop]");
    const dayInput = row.querySelector("[data-task-day]");
    const time = String(timeInput?.value ?? "").trim();
    return {
      sphere: sphereInput?.value || state.config.spheres[0],
      text: String(textInput?.value ?? "").trim(),
      day: dayZone?.dataset.dayDrop || dayInput?.value || state.config.days[0],
      time_minutes: time ? Number(time) : null,
    };
  });
  const stableWarnings = (state.draft?.warnings || []).filter(
    (warning) => !/^(Конфликт нагрузки:|Конфликт: задача |На .+ запланировано \d+ задач\.)/.test(warning),
  );
  return {
    week_label: state.draft?.week_label || state.week.label,
    goals,
    tasks,
    warnings: [...new Set([...stableWarnings, ...clientWarnings(tasks)])],
  };
}

function collectGuidingAnswers() {
  return [...document.querySelectorAll("[data-guiding-answer]")].reduce((answers, input) => {
    const value = input.value.trim();
    if (value) answers[input.dataset.guidingAnswer] = value;
    return answers;
  }, {});
}

function updateComposerState() {
  const text = $("#source-text").value;
  const length = text.length;
  $("#character-count").textContent = `${length} / 8000`;
  $("#generate-button").disabled = !state.user || !hasPlanningDetails(text, collectGuidingAnswers());
}

function hasPlanningDetails(text, answers = {}) {
  const details = [String(text || ""), ...Object.values(answers)]
    .join(" ")
    .replace(/^(Главный результат|Фиксированные дела|Ресурс и время|Не забыть):?\s*$/gim, "")
    .replace(/\s+/g, " ")
    .trim();
  return details.length >= 10;
}

function renderScheduleStatus(schedule) {
  const status = $("#schedule-status");
  const button = $("#sync-schedule");
  const connectButton = $("#connect-skyeng");
  if (!state.user) {
    status.textContent = "Сначала войди в одобренный аккаунт";
    button.disabled = true;
    connectButton.disabled = true;
    connectButton.classList.add("hidden");
    return;
  }
  if (!schedule.connected) {
    status.textContent = schedule.error || "нажми «подключить», чтобы войти в Skyeng";
    button.disabled = true;
    connectButton.disabled = false;
    connectButton.classList.remove("hidden");
  } else if (schedule.error) {
    status.textContent = schedule.error;
    button.disabled = false;
    connectButton.classList.add("hidden");
  } else if (schedule.events.length) {
    status.textContent = `${schedule.events.length} активностей · обновляется раз в неделю`;
    button.disabled = false;
    connectButton.classList.add("hidden");
  } else {
    status.textContent = "активностей на эту неделю не найдено";
    button.disabled = false;
    connectButton.classList.add("hidden");
  }
}

async function loadSchedule(force = false) {
  if (!state.user) {
    renderScheduleStatus({ connected: false, events: [] });
    return;
  }
  const week = state.week;
  const button = $("#sync-schedule");
  button.disabled = true;
  $("#schedule-status").textContent = force ? "обновляю…" : "загружаю…";
  try {
    const result = force
      ? await api("/api/schedule/sync", { method: "POST", body: JSON.stringify({ week_label: week.label, week_start: week.start }) })
      : await api(`/api/schedule?week=${encodeURIComponent(week.label)}&week_start=${encodeURIComponent(week.start)}`);
    renderScheduleStatus(result);
  } catch (error) {
    $("#schedule-status").textContent = error.message;
    button.disabled = false;
  }
}

async function waitForSkyengConnection() {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 5 * 60 * 1000) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const result = await api("/api/skyeng/status");
    if (result.connected) {
      await loadSchedule(true);
      return;
    }
    if (result.status === "failed") {
      throw new Error(result.error || "Окно входа Skyeng закрылось до завершения авторизации.");
    }
  }
  throw new Error("Не дождался входа в Skyeng. Нажми «подключить» и попробуй ещё раз.");
}

async function connectSkyeng() {
  const button = $("#connect-skyeng");
  const status = $("#schedule-status");
  button.disabled = true;
  status.textContent = "открываю окно входа Skyeng…";
  try {
    const result = await api("/api/skyeng/connect", { method: "POST" });
    if (result.connected) {
      await loadSchedule(true);
    } else {
      status.textContent = "войди в открывшемся окне Skyeng…";
      await waitForSkyengConnection();
    }
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
}

async function login() {
  const message = $("#login-message");
  const button = $("#login-form button[type=submit]");
  setMessage(message);
  setLoading(button, true, "Войти");
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login: $("#login").value.trim(), password: $("#login-password").value }),
    });
    showUser(result.user);
    setMessage(message, "Вход выполнен.", "success");
    await loadSchedule();
    $("#composer-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false, "Войти");
  }
}

async function registerAccount() {
  const message = $("#register-message");
  const button = $("#account-register-form button[type=submit]");
  setMessage(message);
  setLoading(button, true, "Отправить заявку");
  try {
    const result = await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        login: $("#register-login").value.trim(),
        password: $("#register-password").value,
        display_name: $("#display-name").value.trim(),
      }),
    });
    setMessage(message, `${result.message} После одобрения можно войти.`, "success");
    $("#account-register-form").reset();
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false, "Отправить заявку");
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  state.user = null;
  state.draft = null;
  $("#auth-guest").classList.remove("hidden");
  $("#connected-state").classList.add("hidden");
  showLogin();
  renderScheduleStatus({ connected: false, events: [] });
  updateComposerState();
}

async function generate() {
  const button = $("#generate-button");
  const message = $("#plan-message");
  const sourceText = $("#source-text").value.trim();
  const guidingAnswers = collectGuidingAnswers();
  if (!hasPlanningDetails(sourceText, guidingAnswers)) {
    setMessage(
      message,
      "Ответь хотя бы на один вопрос или добавь конкретную задачу в описание планов.",
      "error",
    );
    updateComposerState();
    return;
  }
  setMessage(message);
  setLoading(button, true, "Собрать черновик");
  try {
    const result = await api("/api/plan/generate", {
      method: "POST",
      body: JSON.stringify({
        week_label: state.week.label,
        week_start: state.week.start,
        source_text: sourceText,
        guiding_answers: guidingAnswers,
        include_schedule: $("#include-schedule").checked,
      }),
    });
    renderDraft(result.draft);
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false, "Собрать черновик");
    updateComposerState();
  }
}

async function applyCorrection() {
  const input = $("#correction-text");
  const button = $("#apply-correction");
  if (!input.value.trim()) return;
  state.draft = collectDraft();
  setMessage($("#confirm-message"));
  setLoading(button, true);
  try {
    const result = await api("/api/plan/update", {
      method: "POST",
      body: JSON.stringify({
        week_label: state.draft.week_label,
        week_start: state.week.start,
        draft: state.draft,
        correction: input.value.trim(),
        include_schedule: $("#include-schedule").checked,
      }),
    });
    input.value = "";
    renderDraft(result.draft);
  } catch (error) {
    setMessage($("#confirm-message"), error.message);
  } finally {
    setLoading(button, false);
  }
}

async function balancePlan() {
  const input = $("#correction-text");
  input.value = "Сбалансируй нагрузку по дням: сохрани вебинары и явно заданные дни, не допускай больше 180 минут обычных задач в день и не дублируй задачи.";
  await applyCorrection();
}

async function carryOver() {
  const button = $("#carry-over");
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "загружаю…";
  try {
    const result = await api("/api/plan/carry-over", {
      method: "POST",
      body: JSON.stringify({ week_label: state.week.label, week_start: state.week.start }),
    });
    if (!result.source_text) {
      setMessage($("#plan-message"), `На неделе ${result.from_week} незавершённых задач не найдено.`, "success");
      return;
    }
    const textarea = $("#source-text");
    textarea.value = textarea.value.trim()
      ? `${textarea.value.trim()}\n\n${result.source_text}`
      : result.source_text;
    updateComposerState();
    setMessage($("#plan-message"), `Добавил ${result.tasks.length} задач из недели ${result.from_week}.`, "success");
    textarea.focus();
  } catch (error) {
    setMessage($("#plan-message"), error.message);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

async function confirmPlan() {
  const button = $("#confirm-button");
  const message = $("#confirm-message");
  state.draft = collectDraft();
  setMessage(message);
  setLoading(button, true, "Подтвердить и записать");
  try {
    const result = await api("/api/plan/confirm", {
      method: "POST",
      body: JSON.stringify({ week_label: state.draft.week_label, draft: state.draft }),
    });
    $("#review-card").classList.add("hidden");
    $("#success-card").classList.remove("hidden");
    $("#reflection-card").classList.remove("hidden");
    $("#reflection-status").textContent = "Можно заполнить сразу или позже.";
    $("#reflection-message").textContent = "";
    $("#success-copy").textContent = `Добавил строки в «${result.sheet_name}», начиная со строки ${result.start_row}.`;
    $("#sheet-link").href = result.spreadsheet_url;
    document.querySelectorAll(".step")[2].classList.remove("active");
    document.querySelectorAll(".step")[2].classList.add("active");
    $("#success-card").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false, "Подтвердить и записать");
  }
}

async function generateReflection() {
  const button = $("#generate-reflection");
  const message = $("#reflection-message");
  if (!state.draft) return;
  setMessage(message);
  setLoading(button, true, "Сгенерировать и записать");
  try {
    const result = await api("/api/reflection/generate", {
      method: "POST",
      body: JSON.stringify({
        week_label: state.draft.week_label,
        draft: state.draft,
        notes: $("#reflection-notes").value.trim(),
      }),
    });
    $("#reflection-status").textContent = result.reflection;
    setMessage(message, "Рефлексия записана в Google Sheets.", "success");
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false, "Сгенерировать и записать");
  }
}

function startOver() {
  state.draft = null;
  $("#review-card").classList.add("hidden");
  $("#success-card").classList.add("hidden");
  $("#reflection-card").classList.add("hidden");
  $("#source-text").focus();
  document.querySelectorAll(".step").forEach((step, index) => step.classList.toggle("active", index === (state.user ? 1 : 0)));
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    state.config = await api("/api/bootstrap");
    populateWeeks();
    if (state.config.user) showUser(state.config.user);
  } catch (error) {
    setMessage($("#plan-message"), error.message);
  }

  $("#login-form").addEventListener("submit", (event) => { event.preventDefault(); login(); });
  $("#account-register-form").addEventListener("submit", (event) => { event.preventDefault(); registerAccount(); });
  $("#show-register").addEventListener("click", showRegistration);
  $("#show-login").addEventListener("click", showLogin);
  $("#logout").addEventListener("click", logout);
  $("#source-text").addEventListener("input", updateComposerState);
  document.querySelectorAll("[data-guiding-answer]").forEach((input) => input.addEventListener("input", updateComposerState));
  $("#generate-button").addEventListener("click", generate);
  $("#carry-over").addEventListener("click", carryOver);
  $("#connect-skyeng").addEventListener("click", connectSkyeng);
  $("#sync-schedule").addEventListener("click", () => loadSchedule(true));
  $("#week-date").addEventListener("change", (event) => setSelectedWeek(event.target.value));
  $("#previous-week").addEventListener("click", () => {
    const start = parseIsoDate(state.week.start);
    start.setDate(start.getDate() - 7);
    setSelectedWeek(isoDate(start));
  });
  $("#next-week").addEventListener("click", () => {
    const start = parseIsoDate(state.week.start);
    start.setDate(start.getDate() + 7);
    setSelectedWeek(isoDate(start));
  });
  $("#include-schedule").addEventListener("change", () => {
    const status = $("#schedule-status");
    if (state.user && $("#include-schedule").checked) {
      status.textContent = status.textContent.replace(/^Не учитывается автоматически\.\s*/, "");
    } else if (state.user) {
      status.textContent = `Не учитывается автоматически. ${status.textContent}`;
    }
  });
  $("#apply-correction").addEventListener("click", applyCorrection);
  $("#balance-plan").addEventListener("click", balancePlan);
  $("#confirm-button").addEventListener("click", confirmPlan);
  $("#generate-reflection").addEventListener("click", generateReflection);
  $("#new-plan").addEventListener("click", startOver);
  $("#another-plan").addEventListener("click", startOver);
  $("#add-goal").addEventListener("click", () => {
    state.draft = collectDraft();
    if (state.draft.goals.length >= 6) return;
    state.draft.goals.push("");
    renderDraft(state.draft);
    const goals = document.querySelectorAll("[data-goal]");
    goals[goals.length - 1].focus();
  });
  $("#add-task").addEventListener("click", () => {
    state.draft = collectDraft();
    state.draft.tasks.push({ sphere: state.config.spheres[0], text: "", day: state.config.days[0], time_minutes: null });
    renderDraft(state.draft);
    const tasks = document.querySelectorAll("[data-task-text]");
    tasks[tasks.length - 1].focus();
  });
  $("#goals-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-goal]");
    if (!button) return;
    state.draft = collectDraft();
    state.draft.goals.splice(Number(button.dataset.deleteGoal), 1);
    renderDraft(state.draft);
  });
  $("#task-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-task]");
    if (!button) return;
    state.draft = collectDraft();
    state.draft.tasks.splice(Number(button.dataset.deleteTask), 1);
    renderDraft(state.draft);
  });
  $("#task-list").addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-task-index]");
    if (!row) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.taskIndex);
    row.classList.add("dragging");
  });
  $("#task-list").addEventListener("dragend", (event) => {
    const row = event.target.closest("[data-task-index]");
    if (row) row.classList.remove("dragging");
    document.querySelectorAll(".day-dropzone.drag-over").forEach((zone) => zone.classList.remove("drag-over"));
  });
  $("#task-list").addEventListener("dragover", (event) => {
    const zone = event.target.closest("[data-day-drop]");
    if (!zone) return;
    event.preventDefault();
    zone.classList.add("drag-over");
  });
  $("#task-list").addEventListener("dragleave", (event) => {
    const zone = event.target.closest("[data-day-drop]");
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("drag-over");
  });
  $("#task-list").addEventListener("drop", (event) => {
    const zone = event.target.closest("[data-day-drop]");
    if (!zone) return;
    event.preventDefault();
    const index = event.dataTransfer.getData("text/plain");
    const row = document.querySelector(`[data-task-index="${index}"]`);
    if (!row) return;
    const empty = zone.querySelector(".empty-day");
    if (empty) empty.remove();
    zone.appendChild(row);
    zone.classList.remove("drag-over");
    updateDayLoads();
    refreshTaskWarnings();
  });
  $("#task-list").addEventListener("input", (event) => {
    if (event.target.matches("[data-task-time], [data-task-text]")) {
      updateDayLoads();
      refreshTaskWarnings();
    }
  });
  if (state.user) loadSchedule();
  else renderScheduleStatus({ connected: false, events: [] });
  updateComposerState();
});
