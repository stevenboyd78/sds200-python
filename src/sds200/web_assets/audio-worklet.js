"use strict";

const PCMU_SAMPLE_RATE = 8000;
const BUFFER_CAPACITY_SAMPLES = 16000;
const START_THRESHOLD_SAMPLES = 480;

function decodeMulaw(value) {
  const inverted = (~value) & 0xff;
  const sign = inverted & 0x80;
  const exponent = (inverted >> 4) & 0x07;
  const mantissa = inverted & 0x0f;
  let sample = ((mantissa << 3) + 0x84) << exponent;
  sample -= 0x84;
  if (sign !== 0) {
    sample = -sample;
  }
  return Math.max(-1, Math.min(1, sample / 32768));
}

class Sds200PcmuProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(BUFFER_CAPACITY_SAMPLES);
    this.readIndex = 0;
    this.writeIndex = 0;
    this.queuedSamples = 0;
    this.phase = 0;
    this.started = false;
    this.sourceStep = PCMU_SAMPLE_RATE / sampleRate;

    this.port.onmessage = (message) => {
      const data = message.data;
      if (
        data === null ||
        typeof data !== "object" ||
        data.type !== "packet"
      ) {
        return;
      }

      if (data.reset === true) {
        this.reset();
      }

      const gapSamples =
        Number.isInteger(data.gapSamples) && data.gapSamples > 0
          ? data.gapSamples
          : 0;
      this.enqueueSilence(gapSamples);

      if (data.payload instanceof ArrayBuffer) {
        this.enqueuePayload(new Uint8Array(data.payload));
      }
    };
  }

  reset() {
    this.readIndex = 0;
    this.writeIndex = 0;
    this.queuedSamples = 0;
    this.phase = 0;
    this.started = false;
  }

  enqueueSample(sample) {
    if (this.queuedSamples === this.buffer.length) {
      this.readIndex = (this.readIndex + 1) % this.buffer.length;
      this.queuedSamples -= 1;
      this.phase = 0;
    }

    this.buffer[this.writeIndex] = sample;
    this.writeIndex = (this.writeIndex + 1) % this.buffer.length;
    this.queuedSamples += 1;
  }

  enqueueSilence(count) {
    const bounded = Math.min(count, this.buffer.length);
    for (let index = 0; index < bounded; index += 1) {
      this.enqueueSample(0);
    }
  }

  enqueuePayload(payload) {
    for (let index = 0; index < payload.length; index += 1) {
      this.enqueueSample(decodeMulaw(payload[index]));
    }
  }

  peek(offset) {
    const index = (this.readIndex + offset) % this.buffer.length;
    return this.buffer[index];
  }

  consumeOne() {
    if (this.queuedSamples === 0) {
      return;
    }
    this.readIndex = (this.readIndex + 1) % this.buffer.length;
    this.queuedSamples -= 1;
  }

  process(_inputs, outputs) {
    const output = outputs[0][0];

    if (!this.started && this.queuedSamples >= START_THRESHOLD_SAMPLES) {
      this.started = true;
    }

    for (let index = 0; index < output.length; index += 1) {
      if (!this.started || this.queuedSamples < 2) {
        output[index] = 0;
        this.started = false;
        this.phase = 0;
        continue;
      }

      const first = this.peek(0);
      const second = this.peek(1);
      output[index] = first + (second - first) * this.phase;

      this.phase += this.sourceStep;
      while (this.phase >= 1 && this.queuedSamples > 1) {
        this.consumeOne();
        this.phase -= 1;
      }
    }

    return true;
  }
}

registerProcessor("sds200-pcmu", Sds200PcmuProcessor);
