"use strict";
// Press-to-talk page. Records the microphone in the browser, sends a
// 16 kHz mono WAV to the local (or demo) server, plays the tower back.

(() => {
  const $ = (s) => document.querySelector(s);
  const PRE_ROLL_MS = 250;   // kept from before the key press
  const TAIL_MS = 300;       // kept after release, so the callsign isn't cut
  const OUT_RATE = 16000;

  const st = { cfg: null, game: null, done: false, sending: false, lastAccepted: true };
  const opts = { text: true, slow: false, radio: true, surprises: true };

  // ------------------------------------------------------------ helpers

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function status(msg) { $("#status").textContent = msg || ""; }

  async function api(path, body, raw) {
    const init = raw
      ? { method: "POST", headers: { "Content-Type": "audio/wav" }, body: raw }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
    const res = await fetch(path, init);
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) {
      const err = new Error(data.error || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ audio out

  let ctx = null;
  let playing = null;
  function audioCtx() {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function radioChain(ac, input) {
    if (!opts.radio) { input.connect(ac.destination); return; }
    const hp = ac.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 350;
    const lp = ac.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 3000;
    const shaper = ac.createWaveShaper();
    const curve = new Float32Array(256);
    for (let i = 0; i < 256; i++) { const x = i / 128 - 1; curve[i] = Math.tanh(2.2 * x); }
    shaper.curve = curve;
    const gain = ac.createGain(); gain.gain.value = 0.8;
    input.connect(hp).connect(lp).connect(shaper).connect(gain).connect(ac.destination);
  }

  function squelch(ac) {
    if (!opts.radio) return;
    const len = Math.floor(ac.sampleRate * 0.12);
    const buf = ac.createBuffer(1, len, ac.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * 0.05 * (1 - i / len);
    const src = ac.createBufferSource(); src.buffer = buf;
    const hp = ac.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 800;
    src.connect(hp).connect(ac.destination);
    src.start();
  }

  async function play(url) {
    const ac = audioCtx();
    const res = await fetch(url);
    if (!res.ok) throw new Error("audio unavailable");
    const buf = await ac.decodeAudioData(await res.arrayBuffer());
    return new Promise((resolve) => {
      const src = ac.createBufferSource();
      src.buffer = buf;
      radioChain(ac, src);
      src.onended = () => { if (playing === src) playing = null; squelch(ac); resolve(); };
      playing = src;
      src.start();
    });
  }
  function stopPlayback() {
    if (playing) { try { playing.stop(); } catch (e) { /* already stopped */ } playing = null; }
  }

  // ------------------------------------------------------------ the log

  function speakerLabel(t) {
    if (t.speaker === "traffic") return "OTHER AIRCRAFT";
    const unit = t.speaker.toUpperCase();
    return t.toPilot ? unit : `${unit} → OTHER AIRCRAFT`;
  }

  function addTransmission(t) {
    const li = el("li", `tx ${t.speaker}`);
    const head = el("div", "tx-head");
    head.append(el("span", "who", speakerLabel(t)));
    if (t.audio) {
      const b = el("button", "replay", "▶ Replay");
      b.type = "button";
      b.addEventListener("click", () => { stopPlayback(); play(t.audio).catch(() => {}); });
      head.append(b);
    }
    const text = el("span", "text", t.text);
    if (!opts.text) {
      text.classList.add("hidden-text");
      text.title = "Click to show";
      text.addEventListener("click", () => text.classList.remove("hidden-text"), { once: true });
    }
    li.append(head, text);
    $("#log").append(li);
    li.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function addYou(body) {
    const cls = body.sayAgain ? "again" : body.accepted ? "ok" : "ko";
    const li = el("li", `tx you ${cls}`);
    const head = el("div", "tx-head");
    head.append(el("span", "who", "YOU"));
    li.append(head);
    const said = (body.transcript || "").trim();
    li.append(el("span", "text", said ? `“${said}”` : "(nothing heard)"));
    const findings = body.findings || [];
    if (findings.length) {
      const ul = el("ul", "findings");
      findings.forEach((f) => ul.append(el("li", f.level, f.text)));
      li.append(ul);
    }
    if (body.example && !body.sayAgain && (findings.length || !body.accepted)) {
      const ex = el("div", "example");
      ex.append("Standard form: ", el("b", null, body.example));
      li.append(ex);
    }
    $("#log").append(li);
    li.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function setHint(body) {
    const hint = $("#hint");
    hint.replaceChildren();
    if (body.done) {
      hint.append(el("span", "label", "DONE"), "Circuit complete. Start another one when you like.");
      return;
    }
    if (body.retry) {
      hint.append(el("span", "label", "CORRECTION"), "Answer the tower: read back the part it asked for.");
      return;
    }
    hint.append(el("span", "label", "YOUR TURN"), body.hint || "");
  }

  function updateRadio(body) {
    if (body.step) $("#r-step").textContent = `${body.step}/${body.steps}`;
    if (body.station) {
      $("#r-station").textContent = body.station.toUpperCase();
      $("#r-freq").textContent = body.frequency || "";
    }
  }

  function showSummary(s) {
    if (!s) return;
    const li = el("li", "summary");
    const parts = [`Runway ${s.runway}`, s.altimeter, `squawk ${s.squawk}`];
    if (s.surprises && s.surprises.length) parts.push(`surprises: ${s.surprises.join(", ")}`);
    li.append(el("strong", null, "Circuit complete. "), parts.join(" · "));
    $("#log").append(li);
    li.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  async function apply(body, you) {
    if (you) addYou(body);
    st.done = !!body.done;
    st.lastAccepted = you ? !!(body.accepted || body.sayAgain) : true;
    $("#skip").hidden = st.lastAccepted || st.done;
    updateRadio(body);
    const items = body.transmissions || [];
    items.forEach(addTransmission);
    setHint(body);
    for (const t of items) {
      if (!t.audio) continue;
      try { await play(t.audio); } catch (e) { status("Couldn't play the tower's audio; the text is above."); }
    }
    if (body.done) showSummary(body.summary);
  }

  // ------------------------------------------------------------ microphone

  let mic = null;
  let pre = [];
  let rec = null;
  let stopping = false;
  let maxTimer = null;

  async function ensureMic() {
    if (mic) return mic;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("this browser gives no microphone access here (it needs https or localhost)");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const ac = audioCtx();
    await ac.audioWorklet.addModule("/static/recorder-worklet.js");
    const source = ac.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ac, "recorder");
    node.port.onmessage = (e) => onFrame(e.data);
    const mute = ac.createGain(); mute.gain.value = 0;
    source.connect(node).connect(mute).connect(ac.destination);
    mic = { stream, rate: ac.sampleRate };
    return mic;
  }

  function onFrame(f) {
    if (rec) {
      rec.push(f);
      let sum = 0;
      for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
      const rms = Math.sqrt(sum / f.length);
      $("#level").style.height = `${Math.min(100, rms * 400)}%`;
    } else if (mic) {
      pre.push(f);
      const keep = Math.ceil((PRE_ROLL_MS / 1000) * mic.rate / f.length);
      if (pre.length > keep) pre.splice(0, pre.length - keep);
    }
  }

  function pttUi(mode) {
    const b = $("#ptt");
    b.classList.toggle("rec", mode === "rec");
    b.classList.toggle("busy", mode === "busy");
    $("#ptt-text").replaceChildren(
      mode === "rec" ? "Release to send" :
      mode === "busy" ? "Listening to you…" : "Hold ",
    );
    if (mode === "idle") $("#ptt-text").append(el("kbd", null, "Space"), " to talk");
    if (mode !== "rec") $("#level").style.height = "0";
  }

  async function pttDown() {
    if (rec || stopping || st.sending || !st.game || st.done) return;
    stopPlayback();
    try {
      if (!mic) { await ensureMic(); }
    } catch (e) {
      status(`No microphone: ${e.message}. You can type below instead.`);
      return;
    }
    rec = pre.slice();
    pre = [];
    pttUi("rec");
    status("");
    const max = (st.cfg ? st.cfg.maxSeconds : 10) * 1000 - PRE_ROLL_MS - TAIL_MS;
    maxTimer = setTimeout(() => { status("That's the 10-second limit. Sent."); pttUp(true); }, max);
  }

  function pttUp(now) {
    if (!rec || stopping) return;
    stopping = true;
    clearTimeout(maxTimer);
    setTimeout(() => {
      const frames = rec;
      rec = null;
      stopping = false;
      send(frames);
    }, now ? 0 : TAIL_MS);
  }

  function toWav(frames, inRate) {
    let n = 0;
    frames.forEach((f) => { n += f.length; });
    const x = new Float32Array(n);
    let o = 0;
    frames.forEach((f) => { x.set(f, o); o += f.length; });
    const ratio = inRate / OUT_RATE;
    const outN = Math.floor(n / ratio);
    const pcm = new Int16Array(outN);
    for (let i = 0; i < outN; i++) {
      const a = Math.floor(i * ratio);
      const b = Math.max(a + 1, Math.floor((i + 1) * ratio));
      let s = 0;
      for (let j = a; j < b && j < n; j++) s += x[j];
      const v = Math.max(-1, Math.min(1, s / (b - a)));
      pcm[i] = v < 0 ? v * 32768 : v * 32767;
    }
    const buf = new ArrayBuffer(44 + pcm.length * 2);
    const dv = new DataView(buf);
    const w = (p, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(p + i, s.charCodeAt(i)); };
    w(0, "RIFF"); dv.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE");
    w(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, OUT_RATE, true); dv.setUint32(28, OUT_RATE * 2, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    w(36, "data"); dv.setUint32(40, pcm.length * 2, true);
    new Int16Array(buf, 44).set(pcm);
    return { blob: new Blob([buf], { type: "audio/wav" }), seconds: outN / OUT_RATE };
  }

  async function send(frames) {
    const { blob, seconds } = toWav(frames, mic.rate);
    if (seconds < 0.5) { pttUi("idle"); status("Too short: hold the key for as long as you speak."); return; }
    st.sending = true;
    pttUi("busy");
    status(st.cfg && st.cfg.mode === "online" ? "Sending to the demo server…" : "Transcribing on your machine…");
    try {
      const body = await api(`/api/turn?game=${encodeURIComponent(st.game)}`, null, blob);
      status("");
      st.sending = false;
      pttUi("idle");
      await apply(body, true);
    } catch (e) {
      st.sending = false;
      pttUi("idle");
      status(e.status === 404 ? "This circuit has expired. Start a new one." : e.message);
    }
  }

  // ------------------------------------------------------------ actions

  async function newGame() {
    stopPlayback();
    audioCtx();
    status("");
    try {
      // ?seed=1234 replays the same game (handy for a demo recording).
      const seed = new URLSearchParams(location.search).get("seed");
      const body = await api("/api/game", { surprises: opts.surprises, slow: opts.slow, seed: seed || undefined });
      st.game = body.game;
      st.done = false;
      $("#log").replaceChildren();
      $("#intro").hidden = true;
      $("#game").hidden = false;
      $("#dock").hidden = false;
      if (st.cfg && st.cfg.speech) ensureMic().catch((e) => status(`No microphone: ${e.message}. You can type below instead.`));
      await apply(body, false);
    } catch (e) {
      status(`Couldn't start: ${e.message}`);
    }
  }

  async function postSimple(path, extra) {
    if (!st.game || st.sending) return;
    st.sending = true;
    try {
      const body = await api(path, { game: st.game, slow: opts.slow, ...(extra || {}) });
      st.sending = false;
      return body;
    } catch (e) {
      st.sending = false;
      status(e.status === 404 ? "This circuit has expired. Start a new one." : e.message);
      return null;
    }
  }

  async function sayAgain() {
    stopPlayback();
    const body = await postSimple("/api/say-again");
    if (!body) return;
    addYou({ transcript: "Say again", sayAgain: true, findings: [] });
    await apply(body, false);
  }

  async function sendText(text) {
    stopPlayback();
    const body = await postSimple("/api/text", { text });
    if (body) await apply(body, true);
  }

  async function skip() {
    stopPlayback();
    const body = await postSimple("/api/skip");
    if (body) await apply(body, false);
  }

  // ------------------------------------------------------------ wiring

  function bind() {
    ["text", "slow", "radio", "surprises"].forEach((k) => {
      const box = $(`#o-${k}`);
      box.checked = opts[k];
      box.addEventListener("change", () => { opts[k] = box.checked; });
    });
    $("#start").addEventListener("click", newGame);
    $("#new").addEventListener("click", () => {
      $("#intro").hidden = false;
      $("#game").hidden = true;
      $("#dock").hidden = true;
      st.game = null;
      stopPlayback();
    });
    $("#say-again").addEventListener("click", sayAgain);
    $("#skip").addEventListener("click", skip);
    $("#type-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const input = $("#type-input");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      if (/^\s*say again\s*$/i.test(text)) { sayAgain(); return; }
      sendText(text);
    });

    const ptt = $("#ptt");
    ptt.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { ptt.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      pttDown();
    });
    ["pointerup", "pointercancel"].forEach((ev) => ptt.addEventListener(ev, () => pttUp(false)));
    ptt.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });

    document.addEventListener("keydown", (e) => {
      if (e.code !== "Space" || e.repeat || !st.game || $("#dock").hidden) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      pttDown();
    });
    document.addEventListener("keyup", (e) => {
      if (e.code !== "Space" || !rec) return;
      e.preventDefault();
      pttUp(false);
    });
    window.addEventListener("blur", () => { if (rec) pttUp(false); });
  }

  async function init() {
    bind();
    pttUi("idle");
    try {
      const res = await fetch("/api/config");
      st.cfg = await res.json();
    } catch (e) {
      st.cfg = { mode: "local", speech: false, maxSeconds: 10 };
    }
    const c = st.cfg;
    if (c.callsign) {
      $("#r-callsign").textContent = c.callsign;
      $("#i-callsign").textContent = c.callsign;
    }
    if (c.aerodrome) $("#i-aerodrome").textContent = c.aerodrome;
    $("#privacy").textContent = c.mode === "online"
      ? "Online demo: your voice is sent to this server, transcribed in memory by an open-weight Whisper model, then discarded. Nothing is stored or logged. Run it on your own laptop and your voice never leaves it."
      : "Running on your computer: your voice is transcribed here and never leaves this machine.";
    const notes = [];
    if (!c.speech) notes.push("Speech recognition is off on this server: type your transmissions.");
    if (c.mode === "online") notes.push(`Online demo running Whisper ${c.model || ""} on a small CPU server: one transmission at a time, 10 seconds at most.`);
    $("#mode-note").textContent = notes.join(" ");
    $("#ptt").hidden = !c.speech;
  }

  init();
})();
