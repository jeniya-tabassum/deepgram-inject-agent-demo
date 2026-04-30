// =============================================================================
//  InjectAgentMessage demo — browser logic
// =============================================================================
//  This file is intentionally thin. All inject logic, idle timing, and
//  function-call handling lives in agent.py on the server. The browser only:
//
//    - captures mic audio and emits it to the server (Socket.IO 'audio_data')
//    - plays speaker audio it receives from the server ('audio_output')
//    - renders Conversation + Event Timeline panels from server events
//
//  Nothing here decides when to fire an inject. Look in agent.py for that.
// =============================================================================


// ===== State =================================================================

let cfg = null;
let socket = null;
let isRunning = false;
let startTime = 0;

// audio
let audioCtx = null;
let micStream = null;
let micProcessor = null;
let micSource = null;
let playCtx = null;
let nextPlayTime = 0;
let micMuted = false;


// ===== UI helpers ============================================================

function setStatus(text, state) {
  document.getElementById('statusText').textContent = text;
  const dot = document.getElementById('statusDot');
  dot.className = 'status-dot';
  if (state) dot.classList.add(state);
}

function elapsed() {
  return ((performance.now() - startTime)).toFixed(1);
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function addMsg(role, text) {
  const el = document.getElementById('convo');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="role">${role}</div>${esc(text)}`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function addEvent(colorClass, tag, detail) {
  const el = document.getElementById('events');
  const div = document.createElement('div');
  div.className = `evt ${colorClass}`;
  div.innerHTML =
    `<span class="ts">${elapsed()} ms</span>` +
    `<span class="tag">${esc(tag)}</span>` +
    `<span class="detail">${esc(detail || '')}</span>`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function clearAll() {
  document.getElementById('convo').innerHTML = '';
  document.getElementById('events').innerHTML = '';
}

function toggleMic() {
  micMuted = !micMuted;
  const btn = document.getElementById('micBtn');
  btn.classList.toggle('muted', micMuted);
  document.getElementById('micBtnLabel').textContent = micMuted ? 'Unmute Mic' : 'Mute Mic';
  addEvent('dim', micMuted ? 'MIC MUTED' : 'MIC UNMUTED', '');
}


// ===== Color picker for event log ===========================================

function colorForEvent(direction, type) {
  if (type.startsWith('Inject')) return direction === '->' && type.includes('queue') ? 'magenta' : 'yellow';
  if (type === 'AgentStartedSpeaking' || type === 'FunctionCallRequest') return 'green';
  if (type === 'AgentAudioDone' || type === 'Error' || type === 'InjectionRefused' || type === 'Warning') return 'red';
  if (type === 'UserStartedSpeaking') return 'magenta';
  if (type === 'ConversationText' || type === 'Welcome' || type === 'SettingsApplied') return 'blue';
  return 'dim';
}


// ===== Config / device discovery (run on page load) ==========================

async function loadConfig() {
  try {
    const resp = await fetch('/config');
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    cfg = await resp.json();
    if (cfg.error) { setStatus('Server error: ' + cfg.error, 'error'); return; }
    const el = document.getElementById('idleSeconds');
    if (el) el.textContent = cfg.idleNudgeSeconds || 2;
    setStatus('Ready — click Start', '');
  } catch (e) {
    setStatus('Cannot reach server — is server.py running?', 'error');
  }
}

async function loadMics() {
  const sel = document.getElementById('micSelect');
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach(t => t.stop());
    const devices = await navigator.mediaDevices.enumerateDevices();
    sel.innerHTML = '';
    devices.filter(d => d.kind === 'audioinput').forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || 'Microphone';
      sel.appendChild(opt);
    });
  } catch (e) {
    sel.innerHTML = '<option>No mic access</option>';
  }
}

loadConfig();
loadMics();


// ===== Mic capture (16 kHz int16) ============================================

function resampleToInt16(float32, inRate, targetRate) {
  const ratio = inRate / targetRate;
  const outLen = Math.round(float32.length / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const idx = i * ratio;
    const i0 = Math.floor(idx);
    const i1 = Math.min(i0 + 1, float32.length - 1);
    const frac = idx - i0;
    const sample = float32[i0] + (float32[i1] - float32[i0]) * frac;
    const s = Math.max(-1, Math.min(1, sample));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out;
}

async function startMic() {
  const deviceId = document.getElementById('micSelect').value;
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      channelCount: 1,
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    },
  });
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'interactive' });
  micSource = audioCtx.createMediaStreamSource(micStream);
  micProcessor = audioCtx.createScriptProcessor(1024, 1, 1);
  const zeroGain = audioCtx.createGain();
  zeroGain.gain.value = 0;
  micSource.connect(micProcessor);
  micProcessor.connect(zeroGain);
  zeroGain.connect(audioCtx.destination);

  micProcessor.onaudioprocess = (e) => {
    if (!isRunning || !socket || !socket.connected) return;
    const inputData = e.inputBuffer.getChannelData(0);
    let pcm16;
    if (micMuted) {
      const outLen = Math.round(inputData.length * cfg.inputSampleRate / audioCtx.sampleRate);
      pcm16 = new Int16Array(outLen);
    } else {
      pcm16 = resampleToInt16(inputData, audioCtx.sampleRate, cfg.inputSampleRate);
    }
    socket.emit('audio_data', pcm16.buffer);
  };
}

function stopMic() {
  if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
  if (micSource)    { micSource.disconnect();    micSource    = null; }
  if (micStream)    { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (audioCtx && audioCtx.state !== 'closed') { audioCtx.close(); audioCtx = null; }
}


// ===== Audio playback (24 kHz int16 -> speaker) ==============================

function playAudio(arrayBuf) {
  if (!playCtx) {
    playCtx = new (window.AudioContext || window.webkitAudioContext)();
    nextPlayTime = playCtx.currentTime;
  }
  const int16 = new Int16Array(arrayBuf);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;

  const buf = playCtx.createBuffer(1, float32.length, cfg.outputSampleRate);
  buf.getChannelData(0).set(float32);
  const src = playCtx.createBufferSource();
  src.buffer = buf;
  src.connect(playCtx.destination);

  const now = playCtx.currentTime;
  if (nextPlayTime <= now + 0.02) nextPlayTime = now + 0.02;
  src.start(nextPlayTime);
  nextPlayTime += buf.duration;
}

function stopPlayback() {
  nextPlayTime = 0;
  if (playCtx && playCtx.state !== 'closed') { playCtx.close(); playCtx = null; }
}


// ===== Connect / disconnect ==================================================

async function toggleAgent() {
  if (isRunning) { stop(); return; }
  if (!cfg || cfg.error) { alert('Server config not loaded. Is server.py running?'); return; }

  clearAll();

  const btn = document.getElementById('startBtn');
  btn.disabled = true;
  setStatus('Connecting…', '');

  try {
    await startMic();
    startTime = performance.now();
    addEvent('blue', 'SETUP', 'mic open, connecting to server…');

    if (!socket) {
      socket = io();
      wireSocket();
    }
    socket.emit('start_voice_agent');

    isRunning = true;
    btn.disabled = false;
    btn.textContent = 'Stop';
    btn.classList.add('active');
    document.getElementById('micBtn').disabled = false;
    setStatus('Connected — ask "Will it rain today?"', 'connected');
  } catch (err) {
    setStatus(`Error: ${err.message}`, 'error');
    btn.disabled = false;
    stopMic();
  }
}

function stop() {
  isRunning = false;
  if (socket && socket.connected) socket.emit('stop_voice_agent');
  stopMic();
  stopPlayback();

  const btn = document.getElementById('startBtn');
  btn.textContent = 'Start Voice Agent';
  btn.classList.remove('active');
  btn.disabled = false;

  micMuted = false;
  const micBtn = document.getElementById('micBtn');
  micBtn.classList.remove('muted');
  micBtn.disabled = true;
  document.getElementById('micBtnLabel').textContent = 'Mute Mic';

  setStatus('Disconnected', '');
}


// ===== Socket.IO event handlers ==============================================

function wireSocket() {
  socket.on('audio_output', (buf) => {
    if (!isRunning) return;
    playAudio(buf);
  });

  socket.on('event_log', ({ direction, type, body }) => {
    const detail = typeof body === 'string' ? body : JSON.stringify(body, null, 2);
    addEvent(colorForEvent(direction, type), `${direction} ${type}`, detail);
  });

  socket.on('conversation', ({ role, content }) => {
    addMsg(role, content);
  });

  socket.on('user_started_speaking', () => {
    stopPlayback();
  });

  socket.on('connection_error', (msg) => {
    setStatus(`Server error: ${msg}`, 'error');
    stop();
  });

  socket.on('disconnect', () => {
    if (isRunning) {
      addEvent('red', 'WS_CLOSE', 'socket disconnected');
      stop();
    }
  });
}
