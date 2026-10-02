"""Assemble the demo video from what record.cjs captured.

1. Lay the pilot's lines and the tower's messages on one audio track, at
   the moments they were played during the recording, with the same
   radio band-pass the page applies.
2. Mux it with the screen recording.
3. Keep the lines marked "keep" and cut the wait while the server
   transcribes (the opening card says so).
4. Add the captions, the opening and closing cards.

    python demo-video/build.py          # writes demo-video/out/demo.mp4

Needs ffmpeg on the PATH, or `pip install imageio-ffmpeg`.
"""

import base64
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
FPS = 25


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(*args):
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def write_wav(path: Path, pcm: bytes, rate: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def main():
    script = json.loads((HERE / "lines.json").read_text())
    run_ = json.loads((OUT / "run.json").read_text())
    marks = run_["marks"]
    t0 = next(m["t"] for m in marks if m["ev"] == "video_start")
    sec = lambda t: (t - t0) / 1000.0  # noqa: E731

    # 1. audio clips and where they go
    clips = []
    pilot_plays = [e for e in run_["events"] if e["ev"] == "pilot_play"]
    pilot_marks = [m for m in marks if m["ev"] == "pilot"]
    for m, e in zip(pilot_marks, pilot_plays):
        clips.append((OUT / f"pilot_{m['i']:02d}.wav", sec(e["t"]), 300, 3400))
    for k, c in enumerate(run_["clips"]):
        path = OUT / f"tower_{k:02d}.wav"
        write_wav(path, base64.b64decode(c["b64"]), int(c["rate"]))
        clips.append((path, sec(c["t"]), 350, 3000))

    inputs, chains = [], []
    for k, (path, at, lo, hi) in enumerate(clips):
        inputs += ["-i", path]
        ms = max(0, int(at * 1000))
        chains.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,highpass=f={lo},"
                      f"lowpass=f={hi},adelay={ms}:all=1[a{k}]")
    mix = "".join(f"[a{k}]" for k in range(len(clips)))
    graph = ";".join(chains) + f";{mix}amix=inputs={len(clips)}:normalize=0:duration=longest[out]"
    run(*inputs, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", OUT / "audio.wav")

    # 2. screen recording + audio
    run("-i", run_["video"], "-i", OUT / "audio.wav", "-map", "0:v", "-map", "1:a",
        "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", OUT / "full.mp4")

    # 3. the parts to keep
    def mark(ev, i=None, try_=None):
        return [m for m in marks if m["ev"] == ev and (i is None or m.get("i") == i)
                and (try_ is None or m.get("try") == try_)]

    click = sec(mark("click_start")[0]["t"])
    segments = [(click - 1.2, click + 2.2, None)]
    for i, line in enumerate(script["lines"]):
        if not line.get("keep"):
            continue
        caption = OUT / f"caption_{i:02d}.png" if line.get("caption") else None
        # The try that went as planned; earlier tries stay visible in the
        # page's log but are cut, like the waits.
        good = [v["try"] for v in mark("verdict", i) if v.get("asPlanned")]
        t = good[0] if good else 0
        p, r, a, s = (mark(ev, i, t)[0] for ev in ("pilot", "release", "answer", "settled"))
        segments.append((sec(p["t"]) - 0.7, sec(r["t"]) + 0.7, caption))
        segments.append((sec(a["t"]) - 0.3, sec(s["t"]) + 0.4, caption))

    parts = [OUT / "part_open.mp4"]
    card(OUT / "card_open.png", script.get("open_seconds", 6), parts[0])
    for k, (start, end, caption) in enumerate(segments):
        part = OUT / f"part_{k:02d}.mp4"
        args = ["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", OUT / "full.mp4"]
        if caption and caption.exists():
            args += ["-i", caption, "-filter_complex", "[0:v][1:v]overlay=x=40:y=H-h-40[v]",
                     "-map", "[v]", "-map", "0:a"]
        run(*args, "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "1", part)
        parts.append(part)
    end = OUT / "part_end.mp4"
    card(OUT / "card_end.png", script.get("end_seconds", 6), end)
    parts.append(end)

    # 4. one file
    listing = OUT / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    run("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", OUT / "demo.mp4")
    print(f"{OUT / 'demo.mp4'}: {len(segments)} segments")


def card(png: Path, seconds: float, out: Path):
    run("-loop", "1", "-t", seconds, "-i", png, "-f", "lavfi", "-t", seconds,
        "-i", "anullsrc=channel_layout=mono:sample_rate=48000",
        "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "1", "-shortest", out)


if __name__ == "__main__":
    sys.exit(main())
