"use strict";

const FALLBACK_REFRESH_INTERVAL_MS = 2000;
const RECONCILE_INTERVAL_MS = 30000;
const PCMU_HEADER_BYTES = 82;
const PCMU_MAX_FRAME_BYTES = 128 * 1024;
const PCMU_VERSION = 1;
const PCMU_KNOWN_FLAGS = 0x0f;
const PCMU_TIMESTAMP_BACKWARDS = 1 << 3;
const PCMU_EXPECTED_SEQUENCE = 1 << 1;
const PCMU_EXPECTED_TIMESTAMP = 1 << 2;
const MAX_GAP_SAMPLES = 8000;

let currentSnapshot = {};
let eventSource = null;
let eventStreamConnected = false;
let lastEventSequence = null;
let refreshInProgress = false;

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
  startEventStream();
});

window.addEventListener("pagehide", () => {
  stopEventStream();
  stopAudioPlayback();
});

element("audio-play").addEventListener("click", () => {
  void startAudioPlayback();
});
element("audio-stop").addEventListener("click", stopAudioPlayback);

initializeAudioPlayback();
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
