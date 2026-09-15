const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "Что-то пошло не так.");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function statusLabel(status) {
  return { pending: "ожидает одобрения", approved: "одобрен", rejected: "отклонён" }[status] || status;
}

function renderAccounts(accounts) {
  const pending = accounts.filter((account) => account.status === "pending").length;
  $("#accounts-summary").textContent = pending ? `Новых заявок: ${pending}` : "Новых заявок нет";
  $("#accounts-list").innerHTML = accounts.length ? accounts.map((account) => `
    <div class="account-row">
      <div class="account-name"><strong>${escapeHtml(account.display_name)}</strong><small>${escapeHtml(account.login)} · лист: ${escapeHtml(account.sheet_name)}</small></div>
      <span class="account-status ${escapeHtml(account.status)}">${statusLabel(account.status)}</span>
      <div class="account-actions">
        ${account.status !== "approved" ? `<button data-account-action="approve" data-account-id="${account.account_id}" type="button">Одобрить</button>` : ""}
        ${account.status !== "rejected" ? `<button class="reject" data-account-action="reject" data-account-id="${account.account_id}" type="button">Отклонить</button>` : ""}
      </div>
    </div>
  `).join("") : '<p class="message">Заявок пока нет.</p>';
}

async function loadAccounts() {
  try {
    const result = await api("/api/admin/accounts");
    $("#admin-login-card").classList.add("hidden");
    $("#accounts-card").classList.remove("hidden");
    $("#admin-logout").classList.remove("hidden");
    renderAccounts(result.accounts);
  } catch (error) {
    $("#admin-login-card").classList.remove("hidden");
    $("#accounts-card").classList.add("hidden");
    $("#admin-logout").classList.add("hidden");
  }
}

$("#admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#admin-login-message");
  message.textContent = "";
  try {
    await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("#admin-password").value }) });
    await loadAccounts();
  } catch (error) { message.textContent = error.message; }
});

$("#refresh-accounts").addEventListener("click", loadAccounts);
$("#admin-logout").addEventListener("click", async () => { await api("/api/admin/logout", { method: "POST" }); await loadAccounts(); });
$("#accounts-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-account-action]");
  if (!button) return;
  try {
    await api(`/api/admin/accounts/${button.dataset.accountId}/${button.dataset.accountAction}`, { method: "POST" });
    $("#accounts-message").textContent = "Изменения сохранены.";
    $("#accounts-message").className = "message success";
    await loadAccounts();
  } catch (error) { $("#accounts-message").textContent = error.message; }
});

loadAccounts();
