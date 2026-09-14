const state = {
  user: null,
  config: null,
  draft: null,
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

function populateWeeks() {
  const select = $("#week-select");
  select.innerHTML = `
    <option value="${state.config.current_week}">Текущая · ${state.config.current_week}</option>
    <option value="${state.config.next_week}">Следующая · ${state.config.next_week}</option>
  `;
}

function showUser(user) {
  state.user = user;
  $("#connected-name").textContent = user.display_name;
  $("#register-form").classList.add("hidden");
  $("#connected-state").classList.remove("hidden");
  $("#generate-button").disabled = !hasPlanningDetails($("#source-text").value);
  document.querySelectorAll(".step")[0].classList.remove("active");
  document.querySelectorAll(".step")[1].classList.add("active");
}

function showRegistration() {
  $("#register-form").classList.remove("hidden");
  $("#connected-state").classList.add("hidden");
  $("#display-name").focus();
}

function selectOptions(values, selected) {
  return values.map((value) => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function renderDraft(draft) {
  state.draft = draft;
  $("#review-card").classList.remove("hidden");
  $("#success-card").classList.add("hidden");
  $("#goals-list").innerHTML = draft.goals.map((goal, index) => `
    <div class="goal-row">
      <span class="goal-number">${index + 1}</span>
      <input data-goal value="${escapeHtml(goal)}" maxlength="300" aria-label="Цель ${index + 1}">
      <button class="delete-button" type="button" data-delete-goal="${index}" aria-label="Удалить цель">×</button>
    </div>
  `).join("");

  $("#task-list").innerHTML = draft.tasks.map((task, index) => `
    <div class="task-row" data-task-index="${index}">
      <select data-task-sphere aria-label="Сфера">${selectOptions(state.config.spheres, task.sphere)}</select>
      <input data-task-text value="${escapeHtml(task.text)}" maxlength="300" aria-label="Задача">
      <select data-task-day aria-label="День">${selectOptions(state.config.days, task.day)}</select>
      <input data-task-time type="number" min="1" max="1440" placeholder="мин" value="${task.time_minutes ?? ""}" aria-label="Минуты">
      <button class="delete-button" type="button" data-delete-task="${index}" aria-label="Удалить задачу">×</button>
    </div>
  `).join("");

  const warnings = $("#warnings-box");
  $("#warnings-list").innerHTML = (draft.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  warnings.classList.toggle("hidden", !(draft.warnings || []).length);
  document.querySelectorAll(".step")[1].classList.remove("active");
  document.querySelectorAll(".step")[2].classList.add("active");
  $("#review-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function collectDraft() {
  const goals = [...document.querySelectorAll("[data-goal]")]
    .map((input) => input.value.trim())
    .filter(Boolean);
  const tasks = [...document.querySelectorAll("[data-task-index]")].map((row) => {
    const time = row.querySelector("[data-task-time]").value.trim();
    return {
      sphere: row.querySelector("[data-task-sphere]").value,
      task: row.querySelector("[data-task-text]").value.trim(),
      day: row.querySelector("[data-task-day]").value,
      time_minutes: time ? Number(time) : null,
    };
  });
  return { week_label: state.draft.week_label, goals, tasks, warnings: state.draft.warnings || [] };
}

function insertPrompt(text) {
  const textarea = $("#source-text");
  const prefix = textarea.value.trim() ? `${textarea.value.trim()}\n\n` : "";
  textarea.value = `${prefix}${text}`;
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  updateComposerState();
}

function updateComposerState() {
  const text = $("#source-text").value;
  const length = text.length;
  $("#character-count").textContent = `${length} / 8000`;
  $("#generate-button").disabled = !state.user || !hasPlanningDetails(text);
}

function hasPlanningDetails(text) {
  const details = String(text || "")
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
    status.textContent = "Сначала подключи вкладку Google Sheets";
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
  const week = $("#week-select").value;
  const button = $("#sync-schedule");
  button.disabled = true;
  $("#schedule-status").textContent = force ? "обновляю…" : "загружаю…";
  try {
    const result = force
      ? await api("/api/schedule/sync", { method: "POST", body: JSON.stringify({ week_label: week }) })
      : await api(`/api/schedule?week=${encodeURIComponent(week)}`);
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

async function register() {
  const input = $("#display-name");
  const message = $("#register-message");
  const button = $("#register-form button[type=submit]");
  setMessage(message);
  setLoading(button, true);
  try {
    const result = await api("/api/register", { method: "POST", body: JSON.stringify({ display_name: input.value.trim() }) });
    showUser(result.user);
    setMessage(message, "Лист подключён.", "success");
    $("#composer-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    setLoading(button, false);
  }
}

async function generate() {
  const button = $("#generate-button");
  const message = $("#plan-message");
  const sourceText = $("#source-text").value.trim();
  if (!hasPlanningDetails(sourceText)) {
    setMessage(
      message,
      "Добавь хотя бы одну конкретную задачу: что сделать, когда и примерно сколько времени это займёт.",
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
        week_label: $("#week-select").value,
        source_text: sourceText,
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

function startOver() {
  state.draft = null;
  $("#review-card").classList.add("hidden");
  $("#success-card").classList.add("hidden");
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

  $("#register-form").addEventListener("submit", (event) => { event.preventDefault(); register(); });
  $("#source-text").addEventListener("input", updateComposerState);
  $("#generate-button").addEventListener("click", generate);
  $("#connect-skyeng").addEventListener("click", connectSkyeng);
  $("#sync-schedule").addEventListener("click", () => loadSchedule(true));
  $("#week-select").addEventListener("change", () => loadSchedule());
  $("#include-schedule").addEventListener("change", () => {
    const status = $("#schedule-status");
    if (state.user && $("#include-schedule").checked) {
      status.textContent = status.textContent.replace(/^Не учитывается автоматически\.\s*/, "");
    } else if (state.user) {
      status.textContent = `Не учитывается автоматически. ${status.textContent}`;
    }
  });
  $("#apply-correction").addEventListener("click", applyCorrection);
  $("#confirm-button").addEventListener("click", confirmPlan);
  $("#new-plan").addEventListener("click", startOver);
  $("#another-plan").addEventListener("click", startOver);
  $("#change-profile").addEventListener("click", showRegistration);
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
    state.draft.tasks.push({ sphere: state.config.spheres[0], task: "", day: state.config.days[0], time_minutes: null });
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
  document.querySelectorAll("[data-insert]").forEach((button) => button.addEventListener("click", () => insertPrompt(button.dataset.insert)));
  if (state.user) loadSchedule();
  else renderScheduleStatus({ connected: false, events: [] });
  updateComposerState();
});
