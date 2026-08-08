"use strict";

const FALLBACK_REFRESH_INTERVAL_MS = 2000;
const RECONCILE_INTERVAL_MS = 30000;
const RECORDING_REFRESH_INTERVAL_MS = 1000;
const PCMU_HEADER_BYTES = 82;
const PCMU_MAX_FRAME_BYTES = 128 * 1024;
const PCMU_VERSION = 1;
const PCMU_KNOWN_FLAGS = 0x0f;
const PCMU_TIMESTAMP_BACKWARDS = 1 << 3;
const PCMU_EXPECTED_SEQUENCE = 1 << 1;
const PCMU_EXPECTED_TIMESTAMP = 1 << 2;
const MAX_GAP_SAMPLES = 8000;

let currentSnapshot = {};
let currentDaemonHello = {};
let eventSource = null;
let eventStreamConnected = false;
let lastEventSequence = null;
let daemonEventGeneration = 0;
let refreshInProgress = false;
let currentRecording = {};
let recordingStatusAvailable = false;
let recordingRefreshInProgress = false;
let recordingsRefreshInProgress = false;
let recordingMutationInProgress = false;
let scannerControlMutationInProgress = false;

let audioPlaybackGeneration = 0;
let audioPlaybackActive = false;
let audioAbortController = null;
let audioReader = null;
let audioContext = null;
let audioWorkletNode = null;
let audioLastStreamSequence = null;
let audioLastPacketsDropped = null;
let audioLastPayloadBytesDropped = null;
let audioLastOverflows = null;
let audioPacketsReceived = 0;
let audioRtpMissingPackets = 0;
let audioLastTelemetryUpdate = 0;

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

function finiteNumber(value, fallback = 0) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return fallback;
}

function wholeNumber(value, fallback = 0) {
  const number = finiteNumber(value, fallback);
  return Math.max(0, Math.trunc(number));
}

function formatDuration(value) {
  const seconds = Math.max(0, finiteNumber(value));
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainder = wholeSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(
      remainder,
    ).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function formatBytes(value) {
  const bytes = wholeNumber(value);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KiB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function formatRecordedAt(value) {
  if (typeof value !== "string" || value === "") {
    return "Unknown time";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown time";
  }
  return parsed.toLocaleString();
}

function recordingName(identifier) {
  const parts = identifier.split("/");
  return parts[parts.length - 1] || identifier;
}

function recordingFileUrl(identifier) {
  const encoded = identifier
    .split("/")
    .map((component) => encodeURIComponent(component))
    .join("/");
  return `/api/v1/recordings/file/${encoded}`;
}

function setRecordingControls(recording) {
  const status = displayValue(recording.status, "unavailable").toLowerCase();
  const active = recording.active === true;
  const canStart =
    recordingStatusAvailable &&
    !recordingMutationInProgress &&
    recording.closed !== true &&
    !active &&
    ["idle", "stopped", "failed"].includes(status);
  const canStop =
    recordingStatusAvailable &&
    !recordingMutationInProgress &&
    active &&
    status === "recording";

  element("recording-start").disabled = !canStart;
  element("recording-stop").disabled = !canStop;
  element("recordings-refresh").disabled = recordingsRefreshInProgress;
}

function renderRecording(recording, available = true) {
  currentRecording = record(recording);
  recordingStatusAvailable = available;
  const reliability = record(currentRecording.reliability);
  const status = displayValue(currentRecording.status, "Unavailable");

  element("recording-status").textContent = status;
  setText(
    "recording-elapsed",
    formatDuration(currentRecording.elapsed_seconds),
    "0:00",
  );
  setText("recording-packets", wholeNumber(currentRecording.packets), "0");
  setText("recording-samples", wholeNumber(currentRecording.samples), "0");
  setText(
    "recording-audio-duration",
    formatDuration(currentRecording.audio_duration_seconds),
    "0:00",
  );
  setText(
    "recording-rtp-loss",
    `${wholeNumber(reliability.packets_lost)} packets`,
  );
  setText(
    "recording-rtp-order",
    `${wholeNumber(reliability.duplicate_packets)} / ${wholeNumber(
      reliability.late_packets,
    )}`,
  );
  setText(
    "recording-rtp-invalid",
    `${wholeNumber(reliability.malformed_packets)} / ${wholeNumber(
      reliability.unexpected_source_packets,
    )}`,
  );
  setText(
    "recording-discontinuities",
    wholeNumber(reliability.timestamp_discontinuities),
    "0",
  );
  setText("recording-file", currentRecording.recording, "None");
  setRecordingControls(currentRecording);
}

function clearChildren(node) {
  while (node.firstChild !== null) {
    node.removeChild(node.firstChild);
  }
}

function makeRecordingActionLink(label, identifier, download = false) {
  const link = document.createElement("a");
  link.className = "recording-action";
  link.textContent = label;
  link.href = recordingFileUrl(identifier);
  if (download) {
    link.download = recordingName(identifier);
  }
  return link;
}

function playSavedRecording(identifier) {
  const player = element("saved-recording-player");
  const name = recordingName(identifier);
  player.src = recordingFileUrl(identifier);
  player.load();
  element("saved-playback-status").textContent = `Loading ${name}.`;
  void player.play().catch((error) => {
    const message =
      error instanceof Error ? error.message : "Saved recording playback failed.";
    element("saved-playback-status").textContent = message;
  });
}

function renderRecordings(inventory) {
  const list = element("recordings-list");
  clearChildren(list);

  const entries = Array.isArray(inventory.entries) ? inventory.entries : [];
  const total = wholeNumber(inventory.total_entries);

  if (entries.length === 0) {
    const empty = document.createElement("li");
    empty.className = "recording-empty";
    empty.textContent = "No finalized recordings are available.";
    list.appendChild(empty);
    element("recordings-message").textContent = "No finalized recordings.";
    return;
  }

  element("recordings-message").textContent =
    `${entries.length} recent of ${total} finalized recording${
      total === 1 ? "" : "s"
    }.`;

  for (const rawEntry of entries) {
    const entry = record(rawEntry);
    const identifier =
      typeof entry.audio === "string" ? entry.audio : "";
    if (identifier === "") {
      continue;
    }

    const item = document.createElement("li");
    item.className = "recording-item";

    const details = document.createElement("div");
    details.className = "recording-item-details";

    const name = document.createElement("strong");
    name.className = "technical-value";
    name.textContent = recordingName(identifier);
    details.appendChild(name);

    const metadata = document.createElement("span");
    metadata.className = "recording-item-meta";
    metadata.textContent =
      `${formatRecordedAt(entry.recorded_at)} · ${
        entry.duration_seconds === null ||
        entry.duration_seconds === undefined
          ? "Unknown duration"
          : formatDuration(entry.duration_seconds)
      } · ${formatBytes(entry.audio_size_bytes)}`;
    details.appendChild(metadata);
    item.appendChild(details);

    const actions = document.createElement("div");
    actions.className = "recording-item-actions";

    if (entry.playable === true) {
      const play = document.createElement("button");
      play.type = "button";
      play.className = "recording-action";
      play.textContent = "Play";
      play.addEventListener("click", () => {
        playSavedRecording(identifier);
      });
      actions.appendChild(play);
      actions.appendChild(
        makeRecordingActionLink("Download", identifier, true),
      );
    } else {
      const unavailable = document.createElement("span");
      unavailable.className = "recording-unavailable";
      unavailable.textContent = "Not playable";
      actions.appendChild(unavailable);
    }

    item.appendChild(actions);
    list.appendChild(item);
  }
}

async function refreshRecordingStatus() {
  if (recordingRefreshInProgress || document.hidden) {
    return;
  }

  recordingRefreshInProgress = true;
  try {
    const response = await fetch("/api/v1/recording", {
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
    renderRecording(record(payload).recording);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Daemon recording status is unavailable.";
    element("recording-status").textContent = message;
    recordingStatusAvailable = false;
    setRecordingControls(currentRecording);
  } finally {
    recordingRefreshInProgress = false;
  }
}

async function refreshRecordings() {
  if (recordingsRefreshInProgress || document.hidden) {
    return;
  }

  recordingsRefreshInProgress = true;
  setRecordingControls(currentRecording);
  try {
    const response = await fetch("/api/v1/recordings", {
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
    renderRecordings(record(payload).recordings);
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Finalized recordings are unavailable.";
    element("recordings-message").textContent = message;
  } finally {
    recordingsRefreshInProgress = false;
    setRecordingControls(currentRecording);
  }
}

async function performRecordingAction(action) {
  if (recordingMutationInProgress) {
    return;
  }

  recordingMutationInProgress = true;
  setRecordingControls(currentRecording);
  element("recording-status").textContent =
    action === "start" ? "Starting…" : "Stopping…";

  try {
    const response = await fetch(`/api/v1/recording/${action}`, {
      method: "POST",
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

    const wasActive = currentRecording.active === true;
    renderRecording(record(payload).recording);
    if (
      action === "stop" ||
      (wasActive && currentRecording.active !== true)
    ) {
      await refreshRecordings();
    }
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Daemon recording operation failed.";
    element("recording-status").textContent = message;
    await refreshRecordingStatus();
  } finally {
    recordingMutationInProgress = false;
    setRecordingControls(currentRecording);
  }
}

function daemonControlSupported(operation) {
  const operations = Array.isArray(currentDaemonHello.control_operations)
    ? currentDaemonHello.control_operations
    : [];
  return (
    currentDaemonHello.read_only === false &&
    operations.includes(operation)
  );
}

function scannerIndexAvailable(value) {
  return Number.isInteger(value) && value >= 0 && value < 0xffffffff;
}

function setScannerHoldControl(id, scope, available, held) {
  const button = element(id);
  const indicator = element(`${id}-state`);
  button.disabled = !available;
  button.textContent = held ? `Release ${scope}` : `Hold ${scope}`;
  button.setAttribute("aria-pressed", held ? "true" : "false");
  indicator.hidden = !held;
}

function setScannerControls() {
  const radio = record(currentSnapshot.radio_state);
  const idle = !scannerControlMutationInProgress;
  const running = currentSnapshot.state === "running";
  const connected = currentSnapshot.scanner_connected === true;
  const canSelect = idle && running && connected;
  const canHold = daemonControlSupported("scanner.hold_state");
  const canNavigateForward = daemonControlSupported("scanner.next");
  const canNavigateBackward = daemonControlSupported("scanner.previous");
  const channelAvailable =
    scannerIndexAvailable(radio.channel_index) &&
    ["TGID", "ConvFrequency"].includes(radio.channel_kind);
  const systemHeld = radio.system_hold === "On";
  const departmentHeld = radio.department_hold === "On";
  const siteHeld = radio.site_hold === "On";
  const channelHeld = radio.channel_hold === "On";
  const systemHoldKnown = systemHeld || radio.system_hold === "Off";
  const departmentHoldKnown =
    departmentHeld || radio.department_hold === "Off";
  const siteHoldKnown = siteHeld || radio.site_hold === "Off";
  const channelHoldKnown = channelHeld || radio.channel_hold === "Off";

  setScannerHoldControl(
    "scanner-hold-system",
    "system",
    canSelect &&
      canHold &&
      systemHoldKnown &&
      (systemHeld || scannerIndexAvailable(radio.system_index)),
    systemHeld,
  );
  setScannerHoldControl(
    "scanner-hold-department",
    "department",
    canSelect &&
      canHold &&
      departmentHoldKnown &&
      (departmentHeld || scannerIndexAvailable(radio.department_index)),
    departmentHeld,
  );
  setScannerHoldControl(
    "scanner-hold-site",
    "site",
    canSelect &&
      canHold &&
      siteHoldKnown &&
      (siteHeld || scannerIndexAvailable(radio.site_index)),
    siteHeld,
  );
  setScannerHoldControl(
    "scanner-hold-channel",
    "channel",
    canSelect &&
      canHold &&
      channelHoldKnown &&
      (channelHeld || channelAvailable),
    channelHeld,
  );
  element("scanner-next").disabled = !(
    canSelect &&
    canNavigateForward &&
    channelAvailable
  );
  element("scanner-previous").disabled = !(
    canSelect &&
    canNavigateBackward &&
    channelAvailable
  );
  element("scanner-reconnect").disabled = !(
    idle &&
    running &&
    daemonControlSupported("scanner.reconnect")
  );
}

function scannerControlAvailabilityMessage() {
  const operations = Array.isArray(currentDaemonHello.control_operations)
    ? currentDaemonHello.control_operations
    : [];
  if (
    currentDaemonHello.read_only !== false ||
    operations.length === 0
  ) {
    return "Scanner controls are unavailable.";
  }
  if (currentSnapshot.state !== "running") {
    return "Scanner controls require a running daemon runtime.";
  }
  if (currentSnapshot.scanner_connected !== true) {
    return daemonControlSupported("scanner.reconnect")
      ? "Scanner disconnected; reconnect remains available."
      : "Scanner controls are unavailable while disconnected.";
  }
  return "Scanner controls ready.";
}

async function performScannerControl(path, label, body = null) {
  if (scannerControlMutationInProgress) {
    return;
  }

  const eventGenerationAtStart = daemonEventGeneration;
  scannerControlMutationInProgress = true;
  setScannerControls();
  element("scanner-control-status").textContent = `${label}…`;

  try {
    const headers = {Accept: "application/json"};
    const options = {
      method: "POST",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    };
    if (body !== null) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(`/api/v1/scanner/${path}`, options);

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }

    const control = record(record(payload).control);
    const snapshot = record(control.snapshot);
    if (Object.keys(snapshot).length === 0) {
      throw new Error("Scanner control omitted its authoritative snapshot.");
    }

    if (daemonEventGeneration === eventGenerationAtStart) {
      currentSnapshot = snapshot;
      renderSnapshot(currentSnapshot, `${label} completed.`);
    } else {
      await reconcileStatusAfterControl();
    }
    element("scanner-control-status").textContent = `Completed: ${label}.`;
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Scanner control failed.";
    await refreshStatus();
    element("scanner-control-status").textContent = message;
  } finally {
    scannerControlMutationInProgress = false;
    setScannerControls();
  }
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
  const recording = record(snapshot.recording);

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

  if (Object.keys(recording).length > 0) {
    renderRecording(recording);
  }

  setScannerControls();

  const updatedAt = new Date();
  const updateNode = element("last-update");
  updateNode.dateTime = updatedAt.toISOString();
  updateNode.textContent = updatedAt.toLocaleString();
}

function renderStatus(payload) {
  const daemon = record(payload.daemon);
  currentDaemonHello = record(daemon.hello);
  currentSnapshot = record(daemon.snapshot);
  renderSnapshot(currentSnapshot);
  if (!scannerControlMutationInProgress) {
    element("scanner-control-status").textContent =
      scannerControlAvailabilityMessage();
  }
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
    daemonEventGeneration += 1;
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
  daemonEventGeneration += 1;

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
  } else if (kind === "recording.state") {
    const wasActive = currentRecording.active === true;
    currentSnapshot = {
      ...currentSnapshot,
      recording: payload,
    };
    renderRecording(payload);
    if (wasActive && currentRecording.active !== true) {
      void refreshRecordings();
    }
    return;
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

async function fetchStatusPayload() {
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
  return payload;
}

async function reconcileStatusAfterControl() {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const generation = daemonEventGeneration;
    let payload;
    try {
      payload = await fetchStatusPayload();
    } catch {
      // The scanner control has already completed successfully. Preserve the
      // event-derived projection and let normal status refresh reconcile later.
      return;
    }
    if (daemonEventGeneration === generation) {
      renderStatus(payload);
      return;
    }
  }

  // A busy event stream remained authoritative throughout reconciliation.
  // Preserve its ordered projection; periodic status refresh supplies the
  // next complete authoritative snapshot boundary.
}

async function refreshStatus() {
  if (refreshInProgress || document.hidden) {
    return;
  }

  refreshInProgress = true;

  try {
    renderStatus(await fetchStatusPayload());
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
    currentDaemonHello = {};
    setScannerControls();
    if (!scannerControlMutationInProgress) {
      element("scanner-control-status").textContent =
        "Scanner controls are unavailable while daemon status is unavailable.";
    }
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

function setAudioControls(status, active) {
  element("audio-playback-status").textContent = status;
  element("audio-play").disabled = active;
  element("audio-stop").disabled = !active;
}

function resetAudioTelemetry() {
  audioLastStreamSequence = null;
  audioLastPacketsDropped = null;
  audioLastPayloadBytesDropped = null;
  audioLastOverflows = null;
  audioPacketsReceived = 0;
  audioRtpMissingPackets = 0;
  audioLastTelemetryUpdate = 0;
  setText("audio-source", "Unavailable");
  setText("audio-packets", 0);
  setText("audio-queue-loss", "0 packets · 0 overflows");
  setText("audio-rtp-loss", "0 packets");
}

function pcmuMagicMatches(view) {
  return (
    view.getUint8(0) === 0x53 &&
    view.getUint8(1) === 0x44 &&
    view.getUint8(2) === 0x53 &&
    view.getUint8(3) === 0x50
  );
}

function parsePcmuFrame(frame) {
  if (!(frame instanceof Uint8Array) || frame.byteLength < PCMU_HEADER_BYTES) {
    throw new Error("PCMU frame is shorter than its fixed header.");
  }

  const view = new DataView(
    frame.buffer,
    frame.byteOffset,
    frame.byteLength,
  );

  if (!pcmuMagicMatches(view)) {
    throw new Error("PCMU frame magic is incompatible.");
  }

  const version = view.getUint8(4);
  const flags = view.getUint8(5);
  const headerSize = view.getUint16(6, false);
  const frameSize = view.getUint32(8, false);

  if (version !== PCMU_VERSION) {
    throw new Error(`Unsupported PCMU stream version: ${version}.`);
  }
  if ((flags & ~PCMU_KNOWN_FLAGS) !== 0) {
    throw new Error("PCMU frame contains unsupported flags.");
  }
  if (headerSize !== PCMU_HEADER_BYTES) {
    throw new Error("PCMU frame header size is invalid.");
  }
  if (frameSize !== frame.byteLength) {
    throw new Error("PCMU frame size is inconsistent.");
  }
  if (frameSize > PCMU_MAX_FRAME_BYTES) {
    throw new Error("PCMU frame exceeds the browser maximum.");
  }

  const streamSequence = view.getBigUint64(12, false);
  const expectedSequence = view.getUint16(38, false);
  const missingPackets = view.getUint32(40, false);
  const expectedTimestamp = view.getUint32(44, false);
  const missingSamples = view.getUint32(48, false);
  const packetsDropped = view.getBigUint64(52, false);
  const payloadBytesDropped = view.getBigUint64(60, false);
  const overflows = view.getBigUint64(68, false);
  const endpointSize = view.getUint16(76, false);
  const payloadSize = view.getUint32(78, false);

  if (streamSequence === 0n) {
    throw new Error("PCMU stream sequence must be greater than zero.");
  }
  if (
    (flags & PCMU_EXPECTED_SEQUENCE) === 0 &&
    expectedSequence !== 0
  ) {
    throw new Error("PCMU frame has an unexpected sequence value.");
  }
  if (missingPackets > 0 && (flags & PCMU_EXPECTED_SEQUENCE) === 0) {
    throw new Error("PCMU packet loss omitted its expected sequence.");
  }
  if (
    (flags & PCMU_EXPECTED_TIMESTAMP) === 0 &&
    expectedTimestamp !== 0
  ) {
    throw new Error("PCMU frame has an unexpected timestamp value.");
  }
  if (
    (missingSamples > 0 || (flags & PCMU_TIMESTAMP_BACKWARDS) !== 0) &&
    (flags & PCMU_EXPECTED_TIMESTAMP) === 0
  ) {
    throw new Error("PCMU timestamp discontinuity omitted its expectation.");
  }
  if (
    missingSamples > 0 &&
    (flags & PCMU_TIMESTAMP_BACKWARDS) !== 0
  ) {
    throw new Error("PCMU timestamp loss and backwards movement conflict.");
  }
  if (headerSize + endpointSize + payloadSize !== frameSize) {
    throw new Error("PCMU frame body sizes are inconsistent.");
  }

  const endpointStart = headerSize;
  const endpointEnd = endpointStart + endpointSize;
  const endpointBytes = frame.slice(endpointStart, endpointEnd);
  const payload = frame.slice(endpointEnd);
  const endpoint = new TextDecoder("utf-8", {fatal: true}).decode(
    endpointBytes,
  );

  if (endpoint.trim() === "") {
    throw new Error("PCMU packet endpoint is empty.");
  }

  return {
    streamSequence,
    flags,
    missingPackets,
    missingSamples,
    packetsDropped,
    payloadBytesDropped,
    overflows,
    endpoint,
    payload,
  };
}

class PcmuFrameParser {
  constructor(onFrame) {
    this.buffer = new Uint8Array(0);
    this.onFrame = onFrame;
  }

  push(chunk) {
    if (!(chunk instanceof Uint8Array)) {
      throw new Error("PCMU HTTP stream yielded non-binary data.");
    }

    const combined = new Uint8Array(this.buffer.byteLength + chunk.byteLength);
    combined.set(this.buffer, 0);
    combined.set(chunk, this.buffer.byteLength);
    this.buffer = combined;

    while (this.buffer.byteLength >= PCMU_HEADER_BYTES) {
      const view = new DataView(
        this.buffer.buffer,
        this.buffer.byteOffset,
        this.buffer.byteLength,
      );

      if (!pcmuMagicMatches(view)) {
        throw new Error("PCMU HTTP stream lost frame alignment.");
      }

      const frameSize = view.getUint32(8, false);
      if (
        frameSize < PCMU_HEADER_BYTES ||
        frameSize > PCMU_MAX_FRAME_BYTES
      ) {
        throw new Error("PCMU HTTP stream advertised an invalid frame size.");
      }

      if (this.buffer.byteLength < frameSize) {
        break;
      }

      const frame = this.buffer.slice(0, frameSize);
      this.buffer = this.buffer.slice(frameSize);
      this.onFrame(parsePcmuFrame(frame));
    }

    if (this.buffer.byteLength > PCMU_MAX_FRAME_BYTES) {
      throw new Error("PCMU HTTP stream exceeded the pending-frame limit.");
    }
  }

  finish() {
    if (this.buffer.byteLength !== 0) {
      throw new Error("PCMU HTTP stream ended with an incomplete frame.");
    }
  }
}

function renderAudioTelemetry(frame) {
  const now = performance.now();
  if (now - audioLastTelemetryUpdate < 500 && audioPacketsReceived !== 1) {
    return;
  }

  audioLastTelemetryUpdate = now;
  setText("audio-source", frame.endpoint);
  setText("audio-packets", audioPacketsReceived);
  setText(
    "audio-queue-loss",
    `${frame.packetsDropped} packets · ${frame.overflows} overflows`,
  );
  setText("audio-rtp-loss", `${audioRtpMissingPackets} packets`);
}

function deliverPcmuFrame(frame) {
  if (audioWorkletNode === null) {
    throw new Error("Browser audio processor is unavailable.");
  }

  if (
    audioLastStreamSequence !== null &&
    frame.streamSequence <= audioLastStreamSequence
  ) {
    throw new Error("PCMU stream sequence did not advance.");
  }

  if (
    audioLastPacketsDropped !== null &&
    frame.packetsDropped < audioLastPacketsDropped
  ) {
    throw new Error("PCMU dropped-packet counter regressed.");
  }

  if (
    audioLastStreamSequence !== null &&
    audioLastPacketsDropped !== null
  ) {
    const skippedPublications =
      frame.streamSequence - audioLastStreamSequence - 1n;
    const newlyDroppedPackets =
      frame.packetsDropped - audioLastPacketsDropped;
    if (skippedPublications !== newlyDroppedPackets) {
      throw new Error(
        "PCMU stream gap does not match daemon queue-loss counters.",
      );
    }
  }
  if (
    audioLastPayloadBytesDropped !== null &&
    frame.payloadBytesDropped < audioLastPayloadBytesDropped
  ) {
    throw new Error("PCMU dropped-byte counter regressed.");
  }
  if (
    audioLastOverflows !== null &&
    frame.overflows < audioLastOverflows
  ) {
    throw new Error("PCMU overflow counter regressed.");
  }

  const localDroppedSamples =
    audioLastPayloadBytesDropped === null
      ? 0n
      : frame.payloadBytesDropped - audioLastPayloadBytesDropped;
  let gapSamples = BigInt(frame.missingSamples) + localDroppedSamples;
  let reset = (frame.flags & PCMU_TIMESTAMP_BACKWARDS) !== 0;

  if (gapSamples > BigInt(MAX_GAP_SAMPLES)) {
    reset = true;
    gapSamples = 0n;
  }

  audioLastStreamSequence = frame.streamSequence;
  audioLastPacketsDropped = frame.packetsDropped;
  audioLastPayloadBytesDropped = frame.payloadBytesDropped;
  audioLastOverflows = frame.overflows;
  audioPacketsReceived += 1;
  audioRtpMissingPackets += frame.missingPackets;

  const payloadBuffer = frame.payload.buffer;
  audioWorkletNode.port.postMessage(
    {
      type: "packet",
      payload: payloadBuffer,
      gapSamples: Number(gapSamples),
      reset,
    },
    [payloadBuffer],
  );

  if (audioPacketsReceived === 1) {
    setAudioControls("Playing", true);
  }
  renderAudioTelemetry(frame);
}

function releaseAudioResources() {
  if (audioAbortController !== null) {
    audioAbortController.abort();
    audioAbortController = null;
  }

  if (audioReader !== null) {
    void audioReader.cancel().catch(() => {});
    audioReader = null;
  }

  if (audioWorkletNode !== null) {
    audioWorkletNode.disconnect();
    audioWorkletNode = null;
  }

  if (audioContext !== null) {
    void audioContext.close().catch(() => {});
    audioContext = null;
  }
}

function stopAudioPlayback() {
  audioPlaybackGeneration += 1;
  audioPlaybackActive = false;
  releaseAudioResources();
  setAudioControls("Stopped", false);
}

async function startAudioPlayback() {
  if (audioPlaybackActive) {
    return;
  }

  if (
    typeof AudioContext === "undefined" ||
    typeof AudioWorkletNode === "undefined"
  ) {
    setAudioControls("AudioWorklet is not supported by this browser.", false);
    element("audio-play").disabled = true;
    return;
  }

  audioPlaybackActive = true;
  const generation = audioPlaybackGeneration + 1;
  audioPlaybackGeneration = generation;
  resetAudioTelemetry();
  setAudioControls("Connecting…", true);

  try {
    const context = new AudioContext({latencyHint: "interactive"});
    audioContext = context;

    await context.audioWorklet.addModule("/assets/audio-worklet.js");
    if (generation !== audioPlaybackGeneration) {
      return;
    }

    const worklet = new AudioWorkletNode(context, "sds200-pcmu", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    audioWorkletNode = worklet;
    worklet.connect(context.destination);
    await context.resume();

    if (generation !== audioPlaybackGeneration) {
      return;
    }

    const controller = new AbortController();
    audioAbortController = controller;

    const response = await fetch("/api/v1/audio", {
      method: "GET",
      headers: {Accept: "application/octet-stream"},
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    });

    if (!response.ok) {
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      throw new Error(errorMessage(payload, response));
    }

    if (response.body === null) {
      throw new Error("Audio response omitted its streaming body.");
    }

    setAudioControls("Buffering…", true);
    const reader = response.body.getReader();
    audioReader = reader;
    const parser = new PcmuFrameParser(deliverPcmuFrame);

    while (generation === audioPlaybackGeneration) {
      const result = await reader.read();
      if (result.done) {
        parser.finish();
        throw new Error("Audio stream ended.");
      }
      parser.push(result.value);
    }
  } catch (error) {
    if (generation !== audioPlaybackGeneration) {
      return;
    }

    const message =
      error instanceof Error
        ? error.message
        : "Browser audio playback failed.";
    setAudioControls(message, false);
  } finally {
    if (generation === audioPlaybackGeneration) {
      audioPlaybackActive = false;
      releaseAudioResources();
      element("audio-play").disabled = false;
      element("audio-stop").disabled = true;
    }
  }
}

function initializeAudioPlayback() {
  resetAudioTelemetry();

  if (
    typeof AudioContext === "undefined" ||
    typeof AudioWorkletNode === "undefined"
  ) {
    setAudioControls("AudioWorklet is not supported by this browser.", false);
    element("audio-play").disabled = true;
    return;
  }

  setAudioControls("Stopped", false);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopEventStream();
    return;
  }

  void refreshStatus();
  void refreshRecordingStatus();
  void refreshRecordings();
  startEventStream();
});

window.addEventListener("pagehide", () => {
  stopEventStream();
  stopAudioPlayback();
  element("saved-recording-player").pause();
});

element("audio-play").addEventListener("click", () => {
  void startAudioPlayback();
});
element("audio-stop").addEventListener("click", stopAudioPlayback);
element("recording-start").addEventListener("click", () => {
  void performRecordingAction("start");
});
element("recording-stop").addEventListener("click", () => {
  void performRecordingAction("stop");
});
element("recordings-refresh").addEventListener("click", () => {
  void refreshRecordings();
});
function performScannerHoldState(scope) {
  const radio = record(currentSnapshot.radio_state);
  const current = radio[`${scope}_hold`];
  if (current !== "On" && current !== "Off") {
    return;
  }
  const held = current !== "On";
  const action = held ? "Hold" : "Release";
  void performScannerControl(
    `hold/${scope}`,
    `${action} ${scope}`,
    {held},
  );
}

element("scanner-hold-system").addEventListener("click", () => {
  performScannerHoldState("system");
});
element("scanner-hold-department").addEventListener("click", () => {
  performScannerHoldState("department");
});
element("scanner-hold-site").addEventListener("click", () => {
  performScannerHoldState("site");
});
element("scanner-hold-channel").addEventListener("click", () => {
  performScannerHoldState("channel");
});
element("scanner-previous").addEventListener("click", () => {
  void performScannerControl("previous", "Previous channel");
});
element("scanner-next").addEventListener("click", () => {
  void performScannerControl("next", "Next channel");
});
element("scanner-reconnect").addEventListener("click", () => {
  void performScannerControl("reconnect", "Reconnect scanner");
});

const savedRecordingPlayer = element("saved-recording-player");
savedRecordingPlayer.addEventListener("play", () => {
  element("saved-playback-status").textContent =
    "Playing finalized recording.";
});
savedRecordingPlayer.addEventListener("ended", () => {
  element("saved-playback-status").textContent =
    "Saved recording playback finished.";
});
savedRecordingPlayer.addEventListener("error", () => {
  element("saved-playback-status").textContent =
    "Saved recording playback failed.";
});

initializeAudioPlayback();
renderRecording({}, false);
setScannerControls();
void refreshStatus();
void refreshRecordingStatus();
void refreshRecordings();
startEventStream();

window.setInterval(() => {
  if (!eventStreamConnected) {
    void refreshStatus();
  }
}, FALLBACK_REFRESH_INTERVAL_MS);

window.setInterval(() => {
  void refreshStatus();
  if (currentRecording.active !== true) {
    void refreshRecordingStatus();
  }
}, RECONCILE_INTERVAL_MS);

window.setInterval(() => {
  if (currentRecording.active === true) {
    void refreshRecordingStatus();
  }
}, RECORDING_REFRESH_INTERVAL_MS);
