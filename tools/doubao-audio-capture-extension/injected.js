(function installDoubaoAudioCapture() {
  if (window.__doubaoAudioCaptureInstalled) {
    return;
  }
  window.__doubaoAudioCaptureInstalled = true;

  const SAMPLE_RATE = 24000;
  const state = {
    chunks: [],
    totalSamples: 0,
    startedAt: null,
    lastChunkAt: null,
    sampleRate: SAMPLE_RATE
  };

  const originalPostMessage = MessagePort.prototype.postMessage;

  function cloneFloat32(data) {
    const copy = new Float32Array(data.length);
    copy.set(data);
    return copy;
  }

  function capturePayload(payload) {
    if (!payload || payload.message !== "dataIn") {
      return;
    }
    const data = payload.data;
    if (!(data instanceof Float32Array) || data.length === 0) {
      return;
    }
    const copy = cloneFloat32(data);
    state.chunks.push(copy);
    state.totalSamples += copy.length;
    const now = Date.now();
    state.startedAt = state.startedAt || now;
    state.lastChunkAt = now;
  }

  MessagePort.prototype.postMessage = function patchedPostMessage(...args) {
    try {
      capturePayload(args[0]);
    } catch (error) {
      window.__doubaoAudioCaptureLastError = String(error && error.message ? error.message : error);
    }
    return originalPostMessage.apply(this, args);
  };

  window.clearDoubaoAudio = function clearDoubaoAudio() {
    state.chunks = [];
    state.totalSamples = 0;
    state.startedAt = null;
    state.lastChunkAt = null;
    window.__doubaoAudioCaptureLastError = null;
    return true;
  };

  window.getDoubaoAudioStats = function getDoubaoAudioStats() {
    return {
      chunks: state.chunks.length,
      samples: state.totalSamples,
      seconds: state.totalSamples / state.sampleRate,
      sampleRate: state.sampleRate,
      startedAt: state.startedAt,
      lastChunkAt: state.lastChunkAt,
      lastError: window.__doubaoAudioCaptureLastError || null
    };
  };

  window.exportDoubaoWav = function exportDoubaoWav() {
    return buildWavBlob();
  };

  window.downloadDoubaoWav = function downloadDoubaoWav(filename) {
    const blob = buildWavBlob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename || `doubao_reading_${formatTimestamp(new Date())}.wav`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return true;
  };

  window.__doubaoAudioCaptureStats = window.getDoubaoAudioStats();
  setInterval(() => {
    window.__doubaoAudioCaptureStats = window.getDoubaoAudioStats();
  }, 500);

  function buildWavBlob() {
    if (!state.chunks.length || state.totalSamples <= 0) {
      throw new Error("No Doubao audio chunks captured");
    }

    const bytesPerSample = 2;
    const dataBytes = state.totalSamples * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataBytes);
    const view = new DataView(buffer);

    writeAscii(view, 0, "RIFF");
    view.setUint32(4, 36 + dataBytes, true);
    writeAscii(view, 8, "WAVE");
    writeAscii(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, state.sampleRate, true);
    view.setUint32(28, state.sampleRate * bytesPerSample, true);
    view.setUint16(32, bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeAscii(view, 36, "data");
    view.setUint32(40, dataBytes, true);

    let offset = 44;
    for (const chunk of state.chunks) {
      for (let index = 0; index < chunk.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, chunk[index]));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += bytesPerSample;
      }
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  function writeAscii(view, offset, text) {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  }

  function formatTimestamp(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return [
      date.getFullYear(),
      pad(date.getMonth() + 1),
      pad(date.getDate()),
      "_",
      pad(date.getHours()),
      pad(date.getMinutes()),
      pad(date.getSeconds())
    ].join("");
  }
})();
