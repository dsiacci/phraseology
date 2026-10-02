# Demo video

How the demo video is made, so it can be made again after a change to the page.

It is one real game on the online demo, recorded in headless Chromium. A simulated microphone speaks the pilot's lines while Space is held down, as a person would: speech recognition, the readback checker and the tower are the real ones. Only the pilot's voice is synthetic: a French Piper voice (`fr_FR-mls-medium`, speaker 6, trained from scratch on Multilingual LibriSpeech, CC BY 4.0) reading English written the way a French speaker says it, for a real French accent. The tower is `en_US-norman`, the other aircraft `en_GB-cori`, as on the demo.

## Make it

From the repository root, with phraseology installed (`pip install .`):

```bash
python demo-video/make_lines.py                      # the pilot's lines -> demo-video/out/pilot_XX.wav
npm install playwright && npx playwright install chromium
node demo-video/record.cjs                           # plays the game, records the page -> out/raw/, out/run.json
pip install imageio-ffmpeg                           # or any ffmpeg on the PATH
python demo-video/build.py                           # -> demo-video/out/demo.mp4
```

Whisper hears a synthetic accent a little differently from one run to the next. `record.cjs` works in takes, as on a film set: when an exchange the video shows does not go as planned the first time, it starts a new game from the top, up to 8 takes (`TAKES=12` for more), and `build.py` uses the clean take. Nothing on screen is retouched. When no take comes out clean, `record.cjs` exits with code 2: change the spelling of the lines that misfire in `lines.json`, regenerate the lines, and check the start of the game with `node demo-video/record.cjs --until 2`. It takes about five minutes against the online demo (the tower answers about four seconds after each release).

## What the steps do

- `make_lines.py` synthesizes each line of `lines.json` with the pilot's voice.
- `record.cjs` opens the game at a fixed seed (`?seed=71`: runway 27, QNH 1010, a Cherokee on base), replaces the microphone with an audio stream it controls, holds Space, plays a line into that stream, releases Space, and waits for the tower to finish talking. It keeps the tower audio as the page plays it, and the time of every event. It also renders the opening and closing cards and the captions as PNG.
- `build.py` lays the pilot's lines and the tower's messages on one audio track at the times they were played, with the band-pass filter the page applies to the tower, muxes it with the screen recording, keeps the lines marked `keep`, cuts the wait while the server transcribes, and adds the captions and the cards.

## Change it

Everything is in `lines.json`:

- `url`: the game to play (the seed fixes runway, QNH, squawk and surprises);
- `pilot_voice`: any Piper voice, with `#speaker` for a multi-speaker one;
- for each line: `say` (what the voice reads, spelled for the accent), `means` (the English it stands for), `keep` (shown or cut), `caption`, `expect` (`ok` or `corrected`), `expect_tower` (a phrase the tower's answer must contain), `skip_if_wrong` (for a line the video doesn't show: skip it if Whisper mishears it);
- `cards`: the text of the opening and closing cards.

The spellings ("riquouest", "kiou-ènn-eïtch", "at ze flaïïng kleub") come from tests through Whisper `small.en`: a French voice reads with French rules, so the English has to be written the French way to come out with an accent and still be understood. Two words did not come through in any spelling, "eight" and "final", which is why the line with the tower frequency is off camera and the video stops before final.

The cards and captions are styled in `record.cjs` (`CSS`). After a change to the page, run `record.cjs` and `build.py` again; `make_lines.py` only when the lines change.
