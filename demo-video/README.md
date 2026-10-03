# Demo video

How the demo video is made, so it can be made again after a change to the page.

It is one real game on the online demo, recorded in headless Chromium. A simulated microphone speaks the pilot's lines while Space is held down, as a person would: speech recognition, the readback checker and the tower are the real ones. Only the pilot's voice stands in for a person: Kokoro-82M (open weights, Apache 2.0), its French voice reading English, for a French accent. The tower is `en_US-norman`, the other aircraft `en_GB-cori`, as on the demo.

Two other pilot voices were tried and are still supported. A French Piper voice reading English spelled the French way (`fr_FR-mls`) sounded too synthetic. ElevenLabs (`eleven_v3` with a `[strong French accent]` tag) sounded natural but its accent stayed light, and on a free plan its French library voices are not available through the API.

## Make it

From the repository root, with phraseology installed (`pip install .`):

```bash
pip install kokoro-onnx                              # for a Kokoro pilot voice (ELEVENLABS_API_KEY for ElevenLabs)
python demo-video/make_lines.py                      # the pilot's lines -> demo-video/out/pilot_XX.wav
npm install playwright && npx playwright install chromium
python -m phraseology serve &                        # the game to record (or DEMO_URL=https://your-server/?seed=71)
node demo-video/record.cjs                           # plays the game, records the page -> out/raw/, out/run.json
pip install imageio-ffmpeg                           # or any ffmpeg on the PATH
python demo-video/build.py                           # -> demo-video/out/demo.mp4
```

Whisper hears a synthetic accent a little differently from one run to the next. `record.cjs` works in takes, as on a film set: when an exchange the video shows does not go as planned the first time, it starts a new game from the top, up to 8 takes (`TAKES=12` for more), and `build.py` uses the clean take. Nothing on screen is retouched. When no take comes out clean, `record.cjs` exits with code 2: change the spelling of the lines that misfire in `lines.json`, regenerate the lines, and check the start of the game with `node demo-video/record.cjs --until 2`. It takes about five minutes against a two-vCPU server (the tower answers about four seconds after each release).

## What the steps do

- `make_lines.py` synthesizes each line of `lines.json` with the pilot's voice. It keeps track of what it already made (`out/lines.json`), so an unchanged line is not synthesized, or paid for, twice.
- `record.cjs` opens the game at a fixed seed (`?seed=71`: runway 27, QNH 1010, a Cherokee on base), replaces the microphone with an audio stream it controls, holds Space, plays a line into that stream, releases Space, and waits for the tower to finish talking. It keeps the tower audio as the page plays it, and the time of every event. It also renders the opening and closing cards and the captions as PNG.
- `build.py` lays the pilot's lines and the tower's messages on one audio track at the times they were played, with the band-pass filter the page applies to the tower, muxes it with the screen recording, keeps the lines marked `keep`, cuts the wait while the server transcribes, and adds the captions, a "pilot speaking" badge while the pilot talks (the voice stands in for a person), and the cards.

## Change it

Everything is in `lines.json`:

- `url`: the game to play, on a local server by default (`DEMO_URL` records against another one, as for the published video); the seed fixes runway, QNH, squawk and surprises;
- `pilot_voice`: `kokoro:<voice>@<lang>` (a voice or a blend such as `ff_siwis*0.6+am_michael*0.4`; `en-us` for English sounds, `fr-fr` to read with French rules), `elevenlabs:<voice name or id>` (settings under `elevenlabs`), or any Piper voice, with `#speaker` for a multi-speaker one;
- for each line: `say` (what the voice reads, spelled for the accent), `means` (the English it stands for), `keep` (shown or cut), `caption`, `expect` (`ok` or `corrected`), `expect_tower` (a phrase the tower's answer must contain), `skip_if_wrong` (for a line the video doesn't show: skip it if Whisper mishears it);
- `cards`: the text of the opening and closing cards.

With a Piper voice, `say` holds the line spelled for the accent ("riquouest", "kiou-ènn-eïtch", "at ze flaïïng kleub"): a French voice reads with French rules, so the English has to be written the French way to come out with an accent and still be understood. With that voice, "eight" and "final" never came through, which is why the frequency line can be skipped off camera (`skip_if_wrong`) and the video stops before final.

The cards and captions are styled in `record.cjs` (`CSS`). After a change to the page, run `record.cjs` and `build.py` again; `make_lines.py` only when the lines change.
