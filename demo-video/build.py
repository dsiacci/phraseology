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
    # Pilot plays of the last page load (the clean take) pair with its marks.
    take_marks = [m for m in pilot_marks if m.get("take") == max(p.get("take", 0) for p in pilot_marks)]
    for m, e in zip(take_marks, pilot_plays):
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
    take = next(m["take"] for m in marks if m["ev"] == "clean_take")

    def mark(ev, i=None, try_=None):
        return [m for m in marks if m["ev"] == ev and m.get("take", take) == take
                and (i is None or m.get("i") == i) and (try_ is None or m.get("try") == try_)]

    m = {"before_pilot": 0.4, "after_release": 0.35, "before_answer": 0.2, "show_answer": 0.9,
         "before_tower": 0.25, "after_settled": 0.2, **script.get("margins", {})}
    plays = sorted(sec(c["t"]) for c in run_["clips"])
    click = sec(mark("click_start")[0]["t"])
    segments = [(click - 0.6, click + 1.6, None)]
    for i, line in enumerate(script["lines"]):
        if not line.get("keep"):
            continue
        caption = OUT / f"caption_{i:02d}.png" if line.get("caption") else None
        p, r, a, s = (sec(mark(ev, i, 0)[0]["t"]) for ev in ("pilot", "release", "answer", "settled"))
        segments.append((p - m["before_pilot"], r + m["after_release"], caption))
        # The feedback appears, then the tower speaks: cut the wait for its
        # voice to be synthesized in between.
        first = next((x for x in plays if a <= x <= s), None)
        if first is not None and first - a > m["show_answer"] + m["before_tower"]:
            segments.append((a - m["before_answer"], a + m["show_answer"], caption))
            segments.append((first - m["before_tower"], s + m["after_settled"], caption))
        else:
            segments.append((a - m["before_answer"], s + m["after_settled"], caption))

    # When the pilot is speaking (global times), for the badge.
    speaking = [(sec(e["t"]), sec(e["t"]) + e["dur"]) for e in pilot_plays]
    badge = OUT / "pilot_badge.png"

    parts = [OUT / "part_open.mp4"]
    card(OUT / "card_open.png", script.get("open_seconds", 6), parts[0])
    for k, (start, end, caption) in enumerate(segments):
        part = OUT / f"part_{k:02d}.mp4"
        args = ["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", OUT / "full.mp4"]
        chain, last, n = [], "0:v", 1
        if caption and caption.exists():
            args += ["-i", caption]
            chain.append(f"[{last}][{n}:v]overlay=x=40:y=H-h-40[c{n}]")
            last, n = f"c{n}", n + 1
        windows = [(max(a, start) - start, min(b, end) - start) for a, b in speaking if a < end and b > start]
        if windows and badge.exists():
            args += ["-i", badge]
            on = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in windows)
            chain.append(f"[{last}][{n}:v]overlay=x=W-w-40:y=H-h-40:enable='{on}'[c{n}]")
            last, n = f"c{n}", n + 1
        if chain:
            args += ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "0:a"]
        run(*args, "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "1", part)
        parts.append(part)
    end = OUT / "part_end.mp4"
    card(OUT / "card_end.png", script.get("end_seconds", 6), end)
    parts.append(end)

    # 4. one file
    listing = OUT / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    run("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", OUT / "joined.mp4")
    # Web-ready: loudness at -16 LUFS, stereo (some players are quiet or
    # silent with mono AAC), index at the front so playback starts at once.
    run("-i", OUT / "joined.mp4", "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac", "2", "-ar", "48000", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        OUT / "demo.mp4")
    print(f"{OUT / 'demo.mp4'}: {len(segments)} segments")


def card(png: Path, seconds: float, out: Path):
    run("-loop", "1", "-t", seconds, "-i", png, "-f", "lavfi", "-t", seconds,
        "-i", "anullsrc=channel_layout=mono:sample_rate=48000",
        "-r", FPS, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "1", "-shortest", out)


if __name__ == "__main__":
    sys.exit(main())
