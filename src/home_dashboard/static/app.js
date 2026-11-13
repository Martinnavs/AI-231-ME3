"use strict";

// State
let state = null;
let lastPollOk = true;
let pollInterval = null;
let clockInterval = null;

// Intent to slot name mapping
const INTENT_SLOTS = {
  "LIGHT_ON": null,
  "LIGHT_OFF": null,
  "BRIGHTNESS": "PERCENT",
  "COLOR": "COLOR",
  "TEMPERATURE": "DEGREES",
  "TIMER": "DURATION",
  "ALARM": "ALARM_TIME",
  "CALL": "CONTACT",
  "MESSAGE": "CONTACT",
  "CREATE_REMINDER": "TASK",
  "LIST_REMINDERS": null,
};

/* --- Init ---------------------------------------------------------- */

async function init() {
  bindEventListeners();
  await pollState();
  pollInterval = setInterval(pollState, 1000);
  clockInterval = setInterval(updateClock, 1000);
  updateClock();
}

function bindEventListeners() {
  const sendBtn = document.getElementById("send-btn");
  const resetBtn = document.getElementById("reset-btn");
  const intentSelect = document.getElementById("intent-select");

  sendBtn.addEventListener("click", sendCommand);
  resetBtn.addEventListener("click", resetForm);
  intentSelect.addEventListener("change", updateSlotInput);
}

function updateSlotInput() {
  const intent = document.getElementById("intent-select").value;
  const input = document.getElementById("slot-input");
  const slotName = INTENT_SLOTS[intent];
  if (slotName) {
    input.placeholder = `Enter ${slotName}...`;
    input.disabled = false;
  } else {
    input.placeholder = "No slot needed";
    input.disabled = true;
    input.value = "";
  }
}

/* --- Polling -------------------------------------------------------- */

async function pollState() {
  try {
    const response = await fetch("/api/state", {
      method: "GET",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    state = await response.json();
    render(state);
    setConnectionStatus(true);
    lastPollOk = true;
  } catch (error) {
    console.error("Poll failed:", error);
    setConnectionStatus(false);
    lastPollOk = false;
  }
}

function setConnectionStatus(ok) {
  const pill = document.getElementById("connection-pill");
  if (ok) {
    pill.textContent = "live";
    pill.classList.remove("offline");
  } else {
    pill.textContent = "offline";
    pill.classList.add("offline");
  }
}

function updateClock() {
  if (!state || !state.now) {
    return;
  }
  document.getElementById("clock").textContent = state.now;
}

/* --- Rendering ----------------------------------------------------- */

function render(s) {
  if (!s) return;

  renderLights(s.lights);
  renderThermostat(s.thermostat);
  renderTimer(s.timer);
  renderAlarm(s.alarm);
  renderPhone(s.phone);
  renderReminders(s.reminders);
  renderIndicator(s.indicator);
}

function renderLights(lights) {
  if (!lights) return;

  const circle = document.getElementById("lights-circle");
  const text = document.getElementById("lights-text");

  if (lights.on) {
    const opacity = 0.25 + 0.75 * (lights.brightness / 100);
    circle.style.background = lights.css;
    circle.style.opacity = opacity;
    const colorName = lights.color || "unknown";
    text.textContent = `On, ${lights.brightness}%, ${colorName}`;
  } else {
    circle.style.background = "#888888";
    circle.style.opacity = "1";
    text.textContent = "Off";
  }
}

function renderThermostat(thermostat) {
  if (!thermostat) return;
  const deg = thermostat.degrees || 0;
  document.getElementById("thermostat-degrees").textContent = `${deg}°C`;
}

function renderTimer(timer) {
  if (!timer) return;

  const display = timer.display || "00:00:00";
  const state = (timer.state || "idle").toLowerCase();
  const remaining = timer.remaining_s || 0;
  const total = timer.total_s || 0;

  document.getElementById("timer-display").textContent = display;
  document.getElementById("timer-state").textContent = state;

  const panel = document.getElementById("panel-timer");
  if (state === "done") {
    panel.classList.add("highlight");
  } else {
    panel.classList.remove("highlight");
  }

  // Update progress bar
  const progressBar = document.getElementById("timer-progress-bar");
  if (state === "running" && total > 0) {
    const progress = ((total - remaining) / total) * 100;
    progressBar.style.display = "block";
    const inner = progressBar.querySelector("::after") || progressBar;
    progressBar.style.background = `linear-gradient(to right, var(--accent) 0%, var(--accent) ${progress}%, var(--panel-border) ${progress}%, var(--panel-border) 100%)`;
  } else {
    progressBar.style.display = "none";
  }
}

function renderAlarm(alarm) {
  if (!alarm) return;

  const state = (alarm.state || "idle").toLowerCase();
  const display = alarm.display || "No alarm";

  let text;
  if (state === "idle") {
    text = "No alarm";
  } else {
    text = display;
  }

  const displayEl = document.getElementById("alarm-display");
  displayEl.textContent = text;

  const panel = document.getElementById("panel-alarm");
  if (state === "ringing") {
    panel.classList.add("highlight", "pulse");
  } else {
    panel.classList.remove("highlight", "pulse");
  }
}

function renderPhone(phone) {
  if (!phone) return;

  const state = (phone.state || "idle").toLowerCase();
  let text;

  if (state === "calling") {
    const contact = phone.contact || "unknown";
    const relation = phone.relation || "";
    const elapsed = phone.elapsed_s || 0;
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    const time = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    text = `Calling ${contact} (${relation}) ${time}`;
  } else if (state === "messaging") {
    const contact = phone.contact || "unknown";
    text = `Message sent to ${contact}`;
  } else {
    text = "Phone idle";
  }

  document.getElementById("phone-display").textContent = text;
}

function renderReminders(reminders) {
  if (!reminders) return;

  const count = reminders.count || 0;
  const items = reminders.items || [];
  const expanded = reminders.expanded || false;

  const header = document.getElementById("reminders-header");
  header.textContent = count === 1 ? "1 reminder" : `${count} reminders`;

  const list = document.getElementById("reminders-list");
  list.innerHTML = "";

  // Newest first, up to 8
  const toShow = items.slice(0, 8);
  for (const item of toShow) {
    const li = document.createElement("li");
    const task = item.task || "";
    const at = item.at || "";
    li.textContent = `${at} · ${task}`;
    list.appendChild(li);
  }

  const panel = document.getElementById("panel-reminders");
  if (expanded) {
    panel.classList.add("highlight");
  } else {
    panel.classList.remove("highlight");
  }
}

function renderIndicator(indicator) {
  if (!indicator) return;

  const detected = indicator.detected || "—";
  const status = indicator.status || null;
  const history = indicator.history || [];

  document.getElementById("detected").textContent = detected;

  const statusEl = document.getElementById("status");
  if (status) {
    statusEl.textContent = status;
  } else {
    statusEl.textContent = "—";
  }

  const historyList = document.getElementById("history-list");
  historyList.innerHTML = "";

  // Newest first
  for (const item of history) {
    const li = document.createElement("li");
    const time = item.time || "";
    const text = item.text || "";
    const ok = item.ok !== false;

    li.textContent = `${time} · ${text}`;
    if (!ok) {
      li.classList.add("failed");
    }
    historyList.appendChild(li);
  }
}

/* --- Commands ------------------------------------------------------ */

async function sendCommand() {
  const intent = document.getElementById("intent-select").value;
  if (!intent) {
    alert("Select an intent");
    return;
  }

  const slotName = INTENT_SLOTS[intent];
  let slots = {};

  if (slotName) {
    const slotValue = document.getElementById("slot-input").value;
    if (!slotValue) {
      alert(`Enter a value for ${slotName}`);
      return;
    }
    slots[slotName] = slotValue;
  }

  const body = {
    intent: intent,
    slots: slots,
  };

  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const reply = await response.text();
    document.getElementById("command-reply").textContent = reply;
  } catch (error) {
    document.getElementById("command-reply").textContent = `Error: ${error.message}`;
  }
}

function resetForm() {
  document.getElementById("intent-select").value = "";
  document.getElementById("slot-input").value = "";
  document.getElementById("command-reply").textContent = "";
  updateSlotInput();
}

/* --- Start ---------------------------------------------------------- */

init();
