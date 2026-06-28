// ===== WhisperDictation UI logic =====
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  config: null,
  hotkeyChoices: [],
  modelChoices: [],
  stats: { recognized: 0, executed: 0, totalMs: 0 },
};

const STATE_LABELS = {
  loading: "Загрузка",
  ready: "Готов",
  recording: "Запись",
  transcribing: "Распознаю",
  thinking: "Думаю",
  stopped: "Остановлено",
};

// ---- Мост: события из Python ------------------------------------------------
window.__emit = function (msg) {
  const { event, payload } = msg;
  if (event === "status") onStatus(payload);
  else if (event === "log") onLog(payload);
  else if (event === "stats") onStats(payload);
};

function onStatus({ state: st, text }) {
  document.body.className = "state-" + st;
  $("#status-label").textContent = STATE_LABELS[st] || st;
  if (text) $("#status-text").textContent = text;
  else if (st === "ready") $("#status-text").textContent = "Готов к работе";
  else if (st === "recording") $("#status-text").textContent = "Слушаю…";
  else if (st === "thinking") $("#status-text").textContent = "Автопилот думает…";
}

function onStats({ device, model }) {
  $("#chip-device").textContent = device === "cpu" ? "CPU" : "GPU";
  $("#chip-model").textContent = model;
}

function onLog({ kind, text, ms }) {
  const log = $("#log");
  const empty = log.querySelector(".log-empty");
  if (empty) empty.remove();

  const item = document.createElement("div");
  item.className = "log-item";
  const tagLabel = kind === "command" ? "команда" : kind === "text" ? "текст"
    : kind === "autopilot" ? "авто" : kind === "reply" ? "ответ" : "инфо";
  item.innerHTML = `
    <span class="log-tag tag-${kind}">${tagLabel}</span>
    <span class="log-text"></span>
    ${ms != null ? `<span class="log-ms">${ms} мс</span>` : ""}`;
  item.querySelector(".log-text").textContent = text;
  log.prepend(item);

  // лимит элементов
  while (log.children.length > 60) log.lastChild.remove();

  // статистика
  if (kind === "text" || kind === "command") {
    state.stats.recognized++;
    if (kind === "command") state.stats.executed++;
    if (ms != null) state.stats.totalMs += ms;
    renderStats();
  }
}

function renderStats() {
  $("#stat-recognized").textContent = state.stats.recognized;
  $("#stat-executed").textContent = state.stats.executed;
  const n = state.stats.recognized;
  $("#stat-avg").textContent = n ? Math.round(state.stats.totalMs / n) + " мс" : "—";
}

// ---- Навигация --------------------------------------------------------------
$$(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const view = btn.dataset.view;
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + view).classList.remove("hidden");
  });
});

// ---- Toast ------------------------------------------------------------------
let toastTimer = null;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
}

// ---- Загрузка конфига -------------------------------------------------------
async function loadConfig() {
  const data = await window.pywebview.api.get_config();
  state.config = data.config;
  state.hotkeyChoices = data.hotkeyChoices;
  state.modelChoices = data.modelChoices;
  applyConfigToUI();
  renderCommands();
}

function applyConfigToUI() {
  const c = state.config;
  // hero + горячая клавиша
  const hkLabel = c.hotkey_label || friendlyKey(c.hotkey || "f7");
  $("#hotkey-kbd").textContent = hkLabel;
  $("#stat-commands").textContent = Object.keys(c.commands || {}).length;
  $("#set-hotkey").value = c.hotkey || "f7";
  $("#set-hotkey-label").value = hkLabel;
  const md = $("#set-model");
  md.innerHTML = "";
  state.modelChoices.forEach((m) => {
    const o = document.createElement("option");
    o.value = m; o.textContent = m;
    if (m === c.model) o.selected = true;
    md.appendChild(o);
  });

  $("#set-language").value = c.language || "ru";
  $("#set-beam").value = c.beam_size || 5;
  $("#set-prompt").value = c.initial_prompt || "";
  $("#set-sound-cmd").checked = !!c.sound_on_command;
  $("#set-sound-dict").checked = !!c.sound_on_dictation;
  $("#set-space").checked = !!c.add_trailing_space;

  // clipboard hotkey
  const clipLabel = c.clipboard_hotkey_label || friendlyKey(c.clipboard_hotkey || "f9");
  $("#set-clip-hotkey").value = c.clipboard_hotkey || "f9";
  $("#set-clip-hotkey-label").value = clipLabel;

  // fuzzy threshold
  $("#set-fuzzy").value = c.fuzzy_threshold != null ? c.fuzzy_threshold : 80;

  // автопилот
  const ap = c.autopilot || {};
  $("#ap-enabled").checked = ap.enabled !== false;
  $("#ap-wake").value = (ap.wake_words || ["клод"]).join(", ");
  $("#ap-provider").value = ap.provider || "groq";
  $("#ap-model").value = ap.model || "llama-3.3-70b-versatile";
  $("#ap-key").value = ap.api_key || "";
  $("#ap-host").value = ap.host || "http://localhost:11434";
  $("#ap-iters").value = ap.max_iterations || 5;
  $("#ap-tts").checked = !!ap.tts_enabled;
  $("#ap-tts-voice").value = ap.tts_voice || "ru-RU-SvetlanaNeural";
  $("#ap-tts-dictation").checked = !!ap.tts_on_dictation;
  $("#ap-wake-hint").textContent = ((ap.wake_words || ["клод"])[0] || "клод")
    .replace(/^./, (m) => m.toUpperCase());
  $("#ap-state").textContent = ap.enabled !== false ? "включён" : "выключен";
  $("#ap-dot").style.background = ap.enabled !== false ? "var(--green)" : "var(--txt-mute)";
}

// ---- Команды (мульти-действия, как в VoiceAttack) --------------------------
const ACT_TYPES = [
  ["url",     "Открыть сайт"],
  ["run",     "Запустить программу"],
  ["keys",    "Нажать клавиши"],
  ["text",    "Вставить текст"],
  ["wait",    "Пауза (мс)"],
  ["webhook", "Webhook (HTTP POST)"],
];
const ACT_PLACEHOLDER = { url: "https://youtube.com", run: "notepad", keys: "ctrl+c, volume up…", text: "любой текст", wait: "500", webhook: "https://home.assistant/api/..." };
const ACT_SHORT = { url: "сайт", run: "запуск", keys: "клавиши", text: "текст", wait: "пауза", webhook: "webhook" };
const ICON_EDIT = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
const ICON_DEL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>`;

function cmdActions(cmd) {
  if (cmd && Array.isArray(cmd.actions)) return cmd.actions;
  if (cmd && cmd.type) return [{ type: cmd.type, value: cmd.value, repeat: cmd.repeat }];
  return [];
}

function renderCommands() {
  const wrap = $("#cmd-table");
  wrap.innerHTML = "";
  const cmds = state.config.commands || {};
  const keys = Object.keys(cmds);
  if (!keys.length) {
    wrap.innerHTML = `<div class="log-empty">Команд пока нет. Нажми «Добавить».</div>`;
    return;
  }
  keys.forEach((phrase) => {
    const acts = cmdActions(cmds[phrase]);
    const first = acts[0] || { type: "text" };
    const summary = acts.map((a) => {
      const r = a.type === "keys" && a.repeat > 1 ? ` ×${a.repeat}` : "";
      return `${ACT_SHORT[a.type] || a.type} ${a.value || ""}${r}`.trim();
    }).join("  →  ");
    const word = acts.length === 1 ? "шаг" : (acts.length < 5 ? "шага" : "шагов");
    const row = document.createElement("div");
    row.className = "cmd-row";
    row.innerHTML = `
      <span class="cmd-phrase"></span>
      <span class="cmd-type type-${first.type}">${acts.length} ${word}</span>
      <span class="cmd-value"></span>
      <span class="cmd-actions">
        <button class="icon-btn" title="Изменить">${ICON_EDIT}</button>
        <button class="icon-btn danger" title="Удалить">${ICON_DEL}</button>
      </span>`;
    row.querySelector(".cmd-phrase").textContent = phrase;
    row.querySelector(".cmd-value").textContent = summary;
    const [editBtn, delBtn] = row.querySelectorAll(".icon-btn");
    editBtn.addEventListener("click", () => openCmdModal(phrase));
    delBtn.addEventListener("click", () => deleteCommand(phrase));
    wrap.appendChild(row);
  });
}

let editingPhrase = null;
let editingActions = [];

function openCmdModal(phrase) {
  editingPhrase = phrase || null;
  $("#cmd-modal-title").textContent = phrase ? "Изменить команду" : "Новая команда";
  const cmd = phrase ? state.config.commands[phrase] : null;
  editingActions = cmd ? cmdActions(cmd).map((a) => ({ ...a })) : [{ type: "url", value: "" }];
  if (!editingActions.length) editingActions = [{ type: "url", value: "" }];
  $("#cmd-phrase").value = phrase || "";
  $("#cmd-sound").value = cmd && typeof cmd.sound === "boolean" ? (cmd.sound ? "on" : "off") : "";
  renderActionRows();
  $("#cmd-modal").classList.remove("hidden");
  $("#cmd-phrase").focus();
}

function renderActionRows() {
  const list = $("#cmd-actions-list");
  list.innerHTML = "";
  editingActions.forEach((a, i) => {
    const row = document.createElement("div");
    row.className = "action-row";
    const opts = ACT_TYPES.map(([v, t]) => `<option value="${v}"${v === a.type ? " selected" : ""}>${t}</option>`).join("");
    row.innerHTML = `
      <select class="act-type">${opts}</select>
      <input class="act-value" type="text" />
      <input class="act-repeat" type="number" min="1" title="повторов" />
      <button class="act-btn act-move" data-d="-1" type="button" title="вверх">↑</button>
      <button class="act-btn act-move" data-d="1" type="button" title="вниз">↓</button>
      <button class="act-btn act-del" type="button" title="удалить">✕</button>`;
    const typeSel = row.querySelector(".act-type");
    const valInp = row.querySelector(".act-value");
    const repInp = row.querySelector(".act-repeat");
    valInp.value = a.value != null ? a.value : "";
    valInp.placeholder = ACT_PLACEHOLDER[a.type] || "";
    repInp.value = a.repeat || 1;
    repInp.style.display = a.type === "keys" ? "" : "none";
    typeSel.addEventListener("change", () => {
      syncActionsFromDom();
      editingActions[i].type = typeSel.value;
      renderActionRows();
    });
    row.querySelectorAll(".act-move").forEach((b) =>
      b.addEventListener("click", () => moveAction(i, parseInt(b.dataset.d, 10))));
    row.querySelector(".act-del").addEventListener("click", () => delAction(i));
    list.appendChild(row);
  });
}

function syncActionsFromDom() {
  const rows = $("#cmd-actions-list").querySelectorAll(".action-row");
  editingActions = Array.from(rows).map((row) => {
    const type = row.querySelector(".act-type").value;
    const value = row.querySelector(".act-value").value;
    const a = { type, value };
    if (type === "keys") {
      const r = parseInt(row.querySelector(".act-repeat").value, 10);
      if (r > 1) a.repeat = r;
    }
    return a;
  });
}

function addAction() {
  syncActionsFromDom();
  editingActions.push({ type: "keys", value: "" });
  renderActionRows();
}

function moveAction(i, d) {
  syncActionsFromDom();
  const j = i + d;
  if (j < 0 || j >= editingActions.length) return;
  [editingActions[i], editingActions[j]] = [editingActions[j], editingActions[i]];
  renderActionRows();
}

function delAction(i) {
  syncActionsFromDom();
  editingActions.splice(i, 1);
  if (!editingActions.length) editingActions = [{ type: "url", value: "" }];
  renderActionRows();
}

function closeCmdModal() {
  $("#cmd-modal").classList.add("hidden");
  editingPhrase = null;
}

function saveCommand() {
  const phrase = $("#cmd-phrase").value.trim().toLowerCase();
  if (!phrase) { toast("Введите фразу"); return; }
  syncActionsFromDom();
  const actions = editingActions
    .map((a) => ({ ...a, value: (a.value || "").trim() }))
    .filter((a) => a.value !== "");
  if (!actions.length) { toast("Добавьте хотя бы одно действие"); return; }
  const cmd = { actions };
  const snd = $("#cmd-sound").value;
  if (snd === "on") cmd.sound = true;
  else if (snd === "off") cmd.sound = false;
  if (editingPhrase && editingPhrase !== phrase) delete state.config.commands[editingPhrase];
  state.config.commands[phrase] = cmd;
  persist(() => {
    renderCommands();
    $("#stat-commands").textContent = Object.keys(state.config.commands).length;
    closeCmdModal();
    toast("Команда сохранена");
  });
}

function deleteCommand(phrase) {
  delete state.config.commands[phrase];
  persist(() => {
    renderCommands();
    $("#stat-commands").textContent = Object.keys(state.config.commands).length;
    toast("Команда удалена");
  });
}

// ---- Сохранение настроек ----------------------------------------------------
function collectSettings() {
  const c = state.config;
  c.hotkey = $("#set-hotkey").value;
  c.hotkey_label = $("#set-hotkey-label").value;
  c.clipboard_hotkey = $("#set-clip-hotkey").value || "f9";
  c.clipboard_hotkey_label = $("#set-clip-hotkey-label").value || "F9";
  c.fuzzy_threshold = parseInt($("#set-fuzzy").value, 10) || 80;
  c.model = $("#set-model").value;
  c.language = $("#set-language").value.trim() || "ru";
  c.beam_size = parseInt($("#set-beam").value, 10) || 5;
  c.initial_prompt = $("#set-prompt").value;
  c.sound_on_command = $("#set-sound-cmd").checked;
  c.sound_on_dictation = $("#set-sound-dict").checked;
  c.add_trailing_space = $("#set-space").checked;
}

async function persist(after) {
  const res = await window.pywebview.api.save_config(state.config);
  if (res && res.ok) {
    if (after) after();
  } else {
    toast("Ошибка сохранения: " + (res && res.error));
  }
}

function saveSettings() {
  collectSettings();
  persist(() => {
    $("#hotkey-kbd").textContent = state.config.hotkey_label || friendlyKey(state.config.hotkey);
    toast("Настройки сохранены. Перезапусти приложение, если менял модель/клавишу.");
  });
}

function saveAutopilot() {
  const c = state.config;
  const ap = c.autopilot || (c.autopilot = {});
  ap.enabled = $("#ap-enabled").checked;
  ap.wake_words = $("#ap-wake").value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!ap.wake_words.length) ap.wake_words = ["клод"];
  ap.provider = $("#ap-provider").value || "groq";
  ap.model = $("#ap-model").value.trim() || (ap.provider === "groq" ? "llama-3.3-70b-versatile" : "qwen2.5:7b");
  ap.api_key = $("#ap-key").value.trim();
  ap.host = $("#ap-host").value.trim() || "http://localhost:11434";
  ap.max_iterations = parseInt($("#ap-iters").value, 10) || 5;
  ap.tts_enabled = $("#ap-tts").checked;
  ap.tts_voice = $("#ap-tts-voice").value || "ru-RU-SvetlanaNeural";
  ap.tts_on_dictation = $("#ap-tts-dictation").checked;
  persist(() => {
    applyConfigToUI();
    toast("Автопилот сохранён. Перезапусти приложение, если менял модель/сервер.");
  });
}

// ---- Привязка событий -------------------------------------------------------
$("#add-command").addEventListener("click", () => openCmdModal(null));
$("#cmd-cancel").addEventListener("click", closeCmdModal);
$("#cmd-save").addEventListener("click", saveCommand);
$("#cmd-add-action").addEventListener("click", addAction);
$("#cmd-modal").addEventListener("click", (e) => {
  if (e.target.id === "cmd-modal") closeCmdModal();
});
$("#save-settings").addEventListener("click", saveSettings);
$("#save-autopilot").addEventListener("click", saveAutopilot);
$("#clear-ap-context").addEventListener("click", async () => {
  await window.pywebview.api.clear_autopilot_history();
  toast("Контекст разговора сброшен");
});

// ---- Захват горячих клавиш ----
function friendlyKey(h) {
  if (!h) return "—";
  if (String(h).startsWith("vk:")) return "Клавиша " + String(h).slice(3);
  return String(h).toUpperCase();
}
let capturingHotkey = false;
let capturingClipHotkey = false;

$("#hotkey-capture").addEventListener("click", () => {
  capturingHotkey = true;
  capturingClipHotkey = false;
  $("#set-hotkey-label").value = "Нажми клавишу…";
});
$("#clip-hotkey-capture").addEventListener("click", () => {
  capturingClipHotkey = true;
  capturingHotkey = false;
  $("#set-clip-hotkey-label").value = "Нажми клавишу…";
});

window.addEventListener("keydown", (e) => {
  if (!capturingHotkey && !capturingClipHotkey) return;
  e.preventDefault(); e.stopPropagation();
  const code = e.keyCode || e.which || 0;
  let name = e.key;
  if (name === " ") name = "Space";
  else if (name && name.length === 1) name = name.toUpperCase();
  const vkVal = "vk:" + code;
  const label = name || ("Клавиша " + code);
  if (capturingHotkey) {
    $("#set-hotkey").value = vkVal;
    $("#set-hotkey-label").value = label;
    capturingHotkey = false;
  } else {
    $("#set-clip-hotkey").value = vkVal;
    $("#set-clip-hotkey-label").value = label;
    capturingClipHotkey = false;
  }
}, true);
$("#clear-log").addEventListener("click", () => {
  $("#log").innerHTML = `<div class="log-empty">Журнал очищен.</div>`;
});

// ---- Шорткаты моделей (установленные Ollama / список Groq) ------------------
function loadModelShortcuts() {
  if (!window.pywebview || !window.pywebview.api) return;
  const provider = $("#ap-provider").value || "groq";
  const host = $("#ap-host").value || "http://localhost:11434";
  window.pywebview.api.list_models(provider, host).then((res) => {
    const dl = $("#ap-model-list");
    dl.innerHTML = "";
    ((res && res.models) || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m;
      dl.appendChild(o);
    });
  }).catch(() => {});
}
$("#ap-provider").addEventListener("change", loadModelShortcuts);
const apNav = document.querySelector('.nav-item[data-view="autopilot"]');
if (apNav) apNav.addEventListener("click", loadModelShortcuts);

// ---- Импорт / Экспорт команд -----------------------------------------------
$("#export-commands").addEventListener("click", async () => {
  try {
    const json = await window.pywebview.api.export_commands();
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "vox_commands.json"; a.click();
    URL.revokeObjectURL(url);
    toast("Команды экспортированы");
  } catch (e) { toast("Ошибка экспорта: " + e); }
});

$("#import-commands").addEventListener("click", () => $("#import-file").click());
$("#import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const res = await window.pywebview.api.import_commands(text);
    if (res.ok) {
      const data = await window.pywebview.api.get_config();
      state.config = data.config;
      renderCommands();
      $("#stat-commands").textContent = Object.keys(state.config.commands || {}).length;
      toast(`Импортировано ${res.count} команд`);
    } else { toast("Ошибка импорта: " + res.error); }
  } catch (err) { toast("Ошибка чтения файла: " + err); }
  e.target.value = "";
});

// ---- История ----------------------------------------------------------------
let _history = [];
let _historyFilter = "";

function renderHistory() {
  const list = $("#history-list");
  const filter = _historyFilter.toLowerCase();
  const items = filter
    ? _history.filter((h) => h.text.toLowerCase().includes(filter))
    : _history;
  if (!items.length) {
    list.innerHTML = `<div class="log-empty">${filter ? "Ничего не найдено." : "Записей пока нет."}</div>`;
    return;
  }
  list.innerHTML = "";
  items.forEach((h) => {
    const tagLabel = h.kind === "command" ? "команда" : h.kind === "autopilot" ? "авто"
      : h.kind === "reply" ? "ответ" : "текст";
    const row = document.createElement("div");
    row.className = "log-item";
    row.innerHTML = `
      <span class="log-tag tag-${h.kind}">${tagLabel}</span>
      <span class="log-text"></span>
      <span class="log-ms">${h.ts}${h.ms != null ? " · " + h.ms + " мс" : ""}</span>`;
    row.querySelector(".log-text").textContent = h.text;
    list.appendChild(row);
  });
}

async function loadHistory() {
  try {
    _history = await window.pywebview.api.get_history();
    renderHistory();
  } catch (e) {}
}

$("#history-search").addEventListener("input", (e) => {
  _historyFilter = e.target.value.trim();
  renderHistory();
});

$("#clear-history").addEventListener("click", async () => {
  await window.pywebview.api.clear_history();
  _history = [];
  renderHistory();
  toast("История очищена");
});

// Обновляем историю при переходе на вкладку
document.querySelector('.nav-item[data-view="history"]').addEventListener("click", loadHistory);

// Обновляем _history при новых log-событиях (синхронно с onLog)
const _origOnLog = onLog;
window.__onLogHook = function ({ kind, text, ms }) {
  if (kind === "text" || kind === "command" || kind === "autopilot" || kind === "reply") {
    const ts = new Date().toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    _history.unshift({ kind, text, ms, ts });
    if (_history.length > 500) _history.pop();
  }
};

const _wrappedEmit = window.__emit;
window.__emit = function (msg) {
  if (msg.event === "log") window.__onLogHook(msg.payload);
  _wrappedEmit(msg);
};

// ---- Старт ------------------------------------------------------------------
function boot() {
  if (window.pywebview && window.pywebview.api) {
    loadConfig();
  } else {
    window.addEventListener("pywebviewready", loadConfig);
  }
}
boot();
