"use strict";

const REFRESH_INTERVAL_MS = 2000;

let refreshInProgress = false;

function element(id) {
  const node = document.getElementById(id);
  if (node === null) {
    throw new Error(`Dashboard element not found: ${id}`);
  }
  return node;
}

function record(value) {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value)
  ) {
    return value;
  }
  return {};
}

function displayValue(value, fallback = "Unavailable") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function setText(id, value, fallback = "Unavailable") {
  element(id).textContent = displayValue(value, fallback);
}

function booleanLabel(value, trueLabel, falseLabel) {
  if (value === true) {
    return trueLabel;
  }
  if (value === false) {
    return falseLabel;
  }
  return "Unavailable";
}

function signalLabel(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value} / 5`;
  }
  return "Unavailable";
}

function rssiLabel(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return `${value} dBm`;
  }
  return "Unavailable";
}

function psiLabel(snapshot) {
  if (snapshot.psi_active === true) {
    const interval = displayValue(snapshot.psi_interval_ms, "?");
    return `Active · ${interval} ms`;
  }
  if (snapshot.psi_active === false) {
    return "Inactive";
  }
  return "Unavailable";
}

function setOverallStatus(state, label, message) {
  const badge = element("status-badge");
  badge.dataset.state = state;
  badge.textContent = label;
  element("dashboard-message").textContent = message;
}

function renderStatus(payload) {
  const daemon = record(payload.daemon);
  const snapshot = record(daemon.snapshot);
  const radio = record(snapshot.radio_state);
  const audio = record(snapshot.audio);
  const router = record(snapshot.router);

  const connected = snapshot.scanner_connected === true;

  setOverallStatus(
    connected ? "online" : "offline",
    connected ? "Connected" : "Disconnected",
    connected
      ? "Daemon and scanner status are available."
      : "The daemon is available, but the scanner is disconnected.",
  );

  setText(
    "scanner-connected",
    booleanLabel(
      snapshot.scanner_connected,
      "Connected",
      "Disconnected",
    ),
  );
  setText("scanner-model", snapshot.scanner_model, "Unknown model");
  setText(
    "scanner-firmware",
    snapshot.scanner_firmware,
    "Unknown firmware",
  );
  setText("scanner-endpoint", snapshot.scanner_endpoint);

  setText("radio-system", radio.system, "No active system");
  setText("radio-channel", radio.channel, "No active channel");
  setText("radio-mode", radio.mode);
  setText("radio-screen", radio.screen_kind ?? radio.screen);
  setText("radio-signal", signalLabel(radio.signal));
  setText("radio-rssi", rssiLabel(radio.rssi));

  setText("daemon-state", snapshot.state);
  setText("psi-state", psiLabel(snapshot));
  setText(
    "audio-state",
    booleanLabel(audio.running, "Running", "Stopped"),
  );
  setText(
    "router-state",
    booleanLabel(router.running, "Running", "Stopped"),
  );
  setText("transition-sequence", snapshot.transition_sequence);

  const updatedAt = new Date();
  const updateNode = element("last-update");
  updateNode.dateTime = updatedAt.toISOString();
  updateNode.textContent = updatedAt.toLocaleString();
}

function errorMessage(payload, response) {
  const detail = record(payload).detail;
  if (typeof detail === "string" && detail !== "") {
    return detail;
  }
  return `Status request failed with HTTP ${response.status}.`;
}

async function refreshStatus() {
  if (refreshInProgress || document.hidden) {
    return;
  }

  refreshInProgress = true;

  try {
    const response = await fetch("/api/v1/status", {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      cache: "no-store",
      credentials: "same-origin",
    });

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }

    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }

    renderStatus(payload);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "The scanner daemon is unavailable.";

    setOverallStatus("offline", "Unavailable", message);
    setText("scanner-connected", "Unavailable");
    setText("daemon-state", "Unavailable");
    setText("psi-state", "Unavailable");
    setText("audio-state", "Unavailable");
    setText("router-state", "Unavailable");
  } finally {
    refreshInProgress = false;
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    void refreshStatus();
  }
});

void refreshStatus();
window.setInterval(() => {
  void refreshStatus();
}, REFRESH_INTERVAL_MS);
