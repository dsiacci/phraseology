// Plays one game on the demo in headless Chromium and records the page.
// A simulated microphone speaks the pilot's lines (out/pilot_XX.wav) while
// Space is held, exactly as a person would; speech recognition, checking
// and the tower are the real ones. The tower audio is captured as the page
// plays it. Also renders the title cards and captions as PNG.
//
//   npm install playwright && npx playwright install chromium
//   node demo-video/record.cjs
//
// Writes out/raw/*.webm, out/run.json, out/card_*.png, out/caption_XX.png.
const path = require("path");
const fs = require("fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const HERE = __dirname;
const OUT = path.join(HERE, "out");
const script = JSON.parse(fs.readFileSync(path.join(HERE, "lines.json"), "utf8"));
// The page to play: a local server by default, DEMO_URL for another one.
const PAGE_URL = process.env.DEMO_URL || script.url;
const W = 1280, H = 720;

const INIT = `(() => {
  window.__events = [];
  window.__clips = [];
  window.__busyUntil = 0;
  window.__plays = 0;
  const micCtx = new AudioContext();
  const dest = micCtx.createMediaStreamDestination();
  window.__pilot = {
    async say(b64) {
      if (micCtx.state === "suspended") await micCtx.resume();
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const buf = await micCtx.decodeAudioData(bytes.buffer);
      const src = micCtx.createBufferSource();
      src.buffer = buf;
      src.connect(dest);
      return new Promise((res) => {
        src.onended = () => res(buf.duration);
        window.__events.push({ ev: "pilot_play", t: Date.now(), dur: buf.duration });
        src.start();
      });
    },
  };
  if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => dest.stream;
  const orig = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...args) {
    if (this.context !== micCtx && this.buffer && this.buffer.duration > 0.5) {
      const b = this.buffer, ch = b.getChannelData(0);
      const pcm = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) pcm[i] = Math.max(-1, Math.min(1, ch[i])) * 32767;
      const u8 = new Uint8Array(pcm.buffer);
      let s = "";
      for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
      window.__clips.push({ t: Date.now(), rate: b.sampleRate, b64: btoa(s) });
      window.__plays++;
      window.__busyUntil = performance.now() + b.duration * 1000;
    }
    return orig.apply(this, args);
  };
})();`;

let invalid = false;

async function play(browser) {
  // Takes, as on a film set: a shown exchange that misfires ends the take
  // and a new game starts from the top. The video uses the clean take.
  const takes = Number(process.env.TAKES || 8);
  const context = await browser.newContext({
    viewport: { width: W, height: H },
    recordVideo: { dir: path.join(OUT, "raw"), size: { width: W, height: H } },
    bypassCSP: true,
  });
  await context.addInitScript(INIT);
  const page = await context.newPage();
  const marks = [{ ev: "video_start", t: Date.now() }];
  const until = process.argv.includes("--until") ? Number(process.argv[process.argv.indexOf("--until") + 1]) : Infinity;
  let clean = false;
  for (let take = 0; take < takes && !clean; take++) {
    await page.goto(PAGE_URL, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    marks.push({ ev: "click_start", take, t: Date.now() });
    await page.click("#start");
    await page.waitForTimeout(1500);
    clean = true;
    for (const [i, line] of script.lines.entries()) {
      if (i > until) break;
      const ok = await say(page, marks, take, i, line);
      if (!ok) { clean = false; console.log(`take ${take} ends at line ${i}`); break; }
      await page.waitForTimeout(500);
    }
    if (clean) marks.push({ ev: "clean_take", take });
  }
  invalid = !clean;
  await page.waitForTimeout(1200);
  marks.push({ ev: "end", t: Date.now() });
  const clips = await page.evaluate(() => window.__clips);  // this page load = the last take
  const events = await page.evaluate(() => window.__events);
  const video = page.video();
  await context.close();
  fs.writeFileSync(path.join(OUT, "run.json"), JSON.stringify({ marks, events, clips, video: await video.path() }));
}

// One line, with retries for lines the video doesn't show. Returns false
// when a shown exchange did not go as planned on the first try.
async function say(page, marks, take, i, line) {
  const wav = fs.readFileSync(path.join(OUT, `pilot_${String(i).padStart(2, "0")}.wav`)).toString("base64");
  for (let tries = 0; tries < 3; tries++) {
    const tag = { i, take, try: tries };
    const youBefore = await page.locator("#log .tx.you").count();
    const txBefore = await page.locator("#log .tx:not(.you)").count();
    const playsBefore = await page.evaluate(() => window.__plays);
    await page.keyboard.down("Space");
    await page.waitForTimeout(250);
    marks.push({ ev: "pilot", ...tag, t: Date.now() });
    await page.evaluate((b) => window.__pilot.say(b), wav);
    await page.waitForTimeout(300);
    await page.keyboard.up("Space");
    marks.push({ ev: "release", ...tag, t: Date.now() });
    await page.waitForFunction((n) => document.querySelectorAll("#log .tx.you").length > n, youBefore, { timeout: 40000 });
    marks.push({ ev: "answer", ...tag, t: Date.now() });
    await page.waitForTimeout(500);
    const newTx = (await page.locator("#log .tx:not(.you)").count()) - txBefore;
    if (newTx > 0) {
      await page.waitForFunction((n) => window.__plays >= n, playsBefore + newTx, { timeout: 30000 });
      await page.waitForFunction(() => performance.now() > window.__busyUntil + 700, null, { timeout: 30000 });
    }
    marks.push({ ev: "settled", ...tag, t: Date.now() });
    const last = page.locator("#log .tx.you").last();
    const accepted = (await last.getAttribute("class")).includes(" ok");
    const said = await last.locator(".text").innerText();
    const tower = newTx > 0 ? await page.locator("#log .tx:not(.you) .text").last().innerText() : "";
    // The video's captions describe this exchange: it has to happen as planned.
    const asPlanned = accepted === (line.expect === "ok") && (!line.expect_tower || tower.includes(line.expect_tower));
    marks.push({ ev: "verdict", ...tag, accepted, said, tower, asPlanned });
    console.log(`take ${take} line ${i} ${line.step} ${asPlanned ? "as planned" : "not as planned"} | ${said} | ${tower}`);
    if (asPlanned) return true;
    if (line.keep) return false;
    if (line.skip_if_wrong) {
      // A line the video doesn't show: move on, off camera.
      const before = await page.evaluate(() => window.__plays);
      const txSkip = await page.locator("#log .tx:not(.you)").count();
      await page.click("#skip");
      await page.waitForTimeout(500);
      const added = (await page.locator("#log .tx:not(.you)").count()) - txSkip;
      if (added > 0) {
        await page.waitForFunction((n) => window.__plays >= n, before + added, { timeout: 30000 });
        await page.waitForFunction(() => performance.now() > window.__busyUntil + 700, null, { timeout: 30000 });
      }
      marks.push({ ev: "skipped", ...tag, t: Date.now() });
      return true;
    }
  }
  return !line.keep;
}

const CSS = `
  * { box-sizing: border-box; margin: 0; }
  body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .card { width: ${W}px; height: ${H}px; background: #0c1013; color: #e8edf2; display: grid; align-content: center; gap: 22px; padding: 0 110px; }
  .card h1 { font-size: 64px; letter-spacing: -0.01em; }
  .card .sub { font-size: 30px; color: #ffb000; }
  .card p { font-size: 24px; line-height: 1.45; color: #b9c4cf; max-width: 1000px; }
  .card .mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 22px; color: #ffb000; }
  .cap { display: inline-block; background: rgba(12, 16, 19, 0.88); color: #fff; font-size: 30px; font-weight: 600; padding: 12px 22px; border-radius: 10px; border-left: 6px solid #ffb000; }
  .who { display: inline-flex; align-items: center; gap: 16px; background: rgba(12, 16, 19, 0.9); color: #fff; padding: 14px 24px 14px 16px; border-radius: 14px; border: 2px solid #ffb000; }
  .who b { display: block; font-size: 28px; letter-spacing: .06em; }
  .who span { display: block; font-size: 20px; color: #ffcf66; }
`;

// Shown while the pilot speaks: the voice stands in for a person.
const PILOT_BADGE = `<div class="who" id="c">
  <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true">
    <circle cx="32" cy="24" r="12" fill="#e8edf2"/>
    <path d="M10 60c2-13 11-19 22-19s20 6 22 19z" fill="#e8edf2"/>
    <path d="M17 25a15 15 0 0 1 30 0" stroke="#ffb000" stroke-width="4" fill="none"/>
    <rect x="13" y="22" width="7" height="11" rx="3" fill="#ffb000"/>
    <rect x="44" y="22" width="7" height="11" rx="3" fill="#ffb000"/>
    <path d="M16 32c0 7 5 10 12 10" stroke="#ffb000" stroke-width="3" fill="none"/>
    <circle cx="29" cy="42" r="3" fill="#ffb000"/>
  </svg>
  <div><b>PILOT SPEAKING</b><span>a person, on the radio</span></div>
</div>`;

async function stills(browser) {
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  for (const [name, html] of Object.entries(script.cards)) {
    await page.setContent(`<style>${CSS}</style><div class="card">${html}</div>`);
    await page.screenshot({ path: path.join(OUT, `card_${name}.png`) });
  }
  await page.setContent(`<style>${CSS} body { background: transparent; }</style>${PILOT_BADGE}`);
  await page.locator("#c").screenshot({ path: path.join(OUT, "pilot_badge.png"), omitBackground: true });
  for (const [i, line] of script.lines.entries()) {
    if (!line.caption) continue;
    await page.setContent(`<style>${CSS} body { background: transparent; }</style><div style="padding: 0 0 0 0"><span class="cap" id="c"></span></div>`);
    await page.locator("#c").evaluate((el, t) => { el.textContent = t; }, line.caption);
    await page.locator("#c").screenshot({ path: path.join(OUT, `caption_${String(i).padStart(2, "0")}.png`), omitBackground: true });
  }
  await page.close();
}

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  if (!process.argv.includes("--stills-only")) await play(browser);
  await stills(browser);
  await browser.close();
  if (invalid) {
    console.log("No clean take: change the lines that misfire most, or allow more takes (TAKES=12).");
    process.exitCode = 2;
  }
})().catch((e) => { console.error(e); process.exit(1); });
