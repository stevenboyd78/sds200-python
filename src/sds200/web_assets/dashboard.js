"use strict";

const FALLBACK_REFRESH_INTERVAL_MS = 2000;
const RECONCILE_INTERVAL_MS = 30000;

let currentSnapshot = {};
let eventSource = null;
let eventStreamConnected = false;
let lastEventSequence = null;
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

function renderSnapshot(snapshot, message = null) {
  const radio = record(snapshot.radio_state);
  const audio = record(snapshot.audio);
  const router = record(snapshot.router);

  const connected = snapshot.scanner_connected === true;
  const defaultMessage = connected
    ? "Daemon and scanner status are available."
    : "The daemon is available, but the scanner is disconnected.";

  setOverallStatus(
    connected ? "online" : "offline",
    connected ? "Connected" : "Disconnected",
    message ?? defaultMessage,
  );

  setText(
    "scanner-connected",
    booleanLabel(snapshot.scanner_connected, "Connected", "Disconnected"),
  );
  setText("scanner-model", snapshot.scanner_model, "Unknown model");
  setText("scanner-firmware", snapshot.scanner_firmware, "Unknown firmware");
  setText("scanner-endpoint", snapshot.scanner_endpoint);

  setText("radio-system", radio.system, "No active system");
  setText("radio-channel", radio.channel, "No active channel");
  setText("radio-mode", radio.mode);
  setText("radio-screen", radio.screen_kind ?? radio.screen);
  setText("radio-signal", signalLabel(radio.signal));
  setText("radio-rssi", rssiLabel(radio.rssi));

  setText("daemon-state", snapshot.state);
  setText("psi-state", psiLabel(snapshot));
  setText("audio-state", booleanLabel(audio.running, "Running", "Stopped"));
  setText("router-state", booleanLabel(router.running, "Running", "Stopped"));
  setText("transition-sequence", snapshot.transition_sequence);

  const updatedAt = new Date();
  const updateNode = element("last-update");
  updateNode.dateTime = updatedAt.toISOString();
  updateNode.textContent = updatedAt.toLocaleString();
}

function renderStatus(payload) {
  const daemon = record(payload.daemon);
  currentSnapshot = record(daemon.snapshot);
  renderSnapshot(currentSnapshot);
}

function eventSequence(envelope, message) {
  if (
    typeof envelope.sequence === "number" &&
    Number.isInteger(envelope.sequence)
  ) {
    return envelope.sequence;
  }

  const parsed = Number.parseInt(message.lastEventId, 10);
  if (Number.isInteger(parsed)) {
    return parsed;
  }

  throw new Error("Daemon event omitted a valid sequence.");
}

function applyDaemonEvent(envelope, message) {
  const kind = envelope.kind;
  const payload = record(envelope.payload);
  const sequence = eventSequence(envelope, message);

  if (kind === "stream.snapshot") {
    currentSnapshot = payload;
    lastEventSequence = sequence;
    renderSnapshot(currentSnapshot, "Live daemon events are connected.");
    return;
  }

  if (lastEventSequence !== null && sequence !== lastEventSequence + 1) {
    throw new Error(
      `Daemon event sequence gap: expected ${
        lastEventSequence + 1
      }, received ${sequence}.`,
    );
  }
  lastEventSequence = sequence;

  if (kind === "daemon.transition") {
    currentSnapshot = record(payload.snapshot);
  } else if (kind === "scanner.connection") {
    currentSnapshot = {
      ...currentSnapshot,
      scanner_connected: payload.connected,
      scanner_endpoint: payload.endpoint ?? currentSnapshot.scanner_endpoint,
    };
  } else if (kind === "scanner.psi") {
    currentSnapshot = {
      ...currentSnapshot,
      psi_active: true,
      radio_state: record(payload.state),
    };
  } else if (kind === "radio.state") {
    currentSnapshot = {
      ...currentSnapshot,
      radio_state: record(payload.current),
    };
  } else if (kind === "audio.state") {
    currentSnapshot = {
      ...currentSnapshot,
      audio: payload,
    };
  } else if (kind === "destination.health") {
    void refreshStatus();
    return;
  } else {
    return;
  }

  renderSnapshot(currentSnapshot, "Live daemon events are connected.");
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
      headers: {Accept: "application/json"},
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

function stopEventStream() {
  if (eventSource !== null) {
    eventSource.close();
    eventSource = null;
  }
  eventStreamConnected = false;
  lastEventSequence = null;
}

function startEventStream() {
  stopEventStream();

  if (document.hidden || typeof EventSource === "undefined") {
    return;
  }

  eventSource = new EventSource("/api/v1/events");

  eventSource.onopen = () => {
    eventStreamConnected = true;
  };

  eventSource.onmessage = (message) => {
    try {
      const envelope = JSON.parse(message.data);
      if (record(envelope) !== envelope) {
        throw new Error("Daemon event envelope is not an object.");
      }
      eventStreamConnected = true;
      applyDaemonEvent(envelope, message);
    } catch {
      eventStreamConnected = false;
      stopEventStream();
      void refreshStatus();
      window.setTimeout(startEventStream, FALLBACK_REFRESH_INTERVAL_MS);
    }
  };

  eventSource.onerror = () => {
    eventStreamConnected = false;
    element("dashboard-message").textContent =
      "Live events are reconnecting; status polling remains active.";
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopEventStream();
    return;
  }

  void refreshStatus();
  startEventStream();
});

window.addEventListener("pagehide", stopEventStream);

void refreshStatus();
startEventStream();

window.setInterval(() => {
  if (!eventStreamConnected) {
    void refreshStatus();
  }
}, FALLBACK_REFRESH_INTERVAL_MS);

window.setInterval(() => {
  void refreshStatus();
}, RECONCILE_INTERVAL_MS);
