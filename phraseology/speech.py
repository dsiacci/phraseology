"""The open models: Whisper hears, Piper speaks. Neither decides anything.

Audio stays in memory as numpy arrays and bytes. Nothing here writes a
file or logs what was said.
"""

from __future__ import annotations

import io
import os
import threading
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

DEFAULT_VOICES_DIR = Path(os.environ.get(
    "PHRASEOLOGY_VOICES", Path.home() / ".local" / "share" / "phraseology" / "voices"))


# ------------------------------------------------------------------ audio

def wav_to_array(data: bytes) -> tuple[np.ndarray, float]:
    """Decode a mono 16-bit PCM WAV into float32 samples at 16 kHz.

    Returns (samples, duration in seconds). Raises ValueError on
    anything else, so a bad upload gets a clean 400.
    """
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate, frames = (w.getnchannels(), w.getsampwidth(),
                                             w.getframerate(), w.getnframes())
            if width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise ValueError("expected 16-bit PCM WAV, 8 to 48 kHz")
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a WAV file: {exc}") from exc
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    duration = len(x) / rate
    if rate != SAMPLE_RATE and len(x):
        n = int(round(len(x) * SAMPLE_RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x, duration


def array_to_wav(x: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------------------ hearing

def build_prompt(aerodrome, callsign) -> str:
    """Vocabulary for Whisper's initial prompt.

    Only words that never change during a game: station names, radio
    words, runway and taxiway names, the callsign. No QNH, squawk or
    wind: a value in the prompt pulls the transcript towards it, and a
    wrong readback would come out right. The order matters too: a prompt
    that ends with "say again" makes Whisper skip a "say again" at the
    start of the clip, so the callsign goes last.
    """
    from .icao import ALPHABET, say_designator
    name = aerodrome.name
    rwys = ", ".join(f"runway {r}" for r in aerodrome.runways)
    hps = ", ".join(say_designator(r["holding_point"]) for r in aerodrome.runways.values())
    twys = ", ".join(f"via {ALPHABET[t]}" for t in (aerodrome.taxiway, aerodrome.exit_taxiway))
    return (f"{name} Ground, {name} Tower. Say again, wilco, roger, QNH, QFE, hectopascals, "
            "squawk, decimal, line up and wait, cleared for take-off, downwind, touch and go, "
            "final, cleared to land, full stop, vacate left, vacate right, number 2, in sight, "
            f"{rwys}, holding point {hps}, {twys}. "
            f"{callsign.spoken_full}, {callsign.spoken_short}.")


class Transcriber:
    """faster-whisper on the CPU, int8. One transcription at a time."""

    def __init__(self, model: str = "small.en", threads: int = 0, beam_size: int = 5,
                 vad: bool = True):
        from faster_whisper import WhisperModel
        self.name = model
        self.beam_size = beam_size
        self.vad = vad
        try:  # offline once downloaded: no network call at start
            self.model = WhisperModel(model, device="cpu", compute_type="int8",
                                      cpu_threads=threads, local_files_only=True)
        except Exception:
            self.model = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
        self.lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> str:
        if len(audio) < SAMPLE_RATE // 4 or float(np.max(np.abs(audio))) < 0.01:
            return ""
        # A little silence on both sides: Whisper tends to drop a word that
        # starts or ends right at the edge of the clip.
        pad = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)
        audio = np.concatenate([pad, audio.astype(np.float32), pad, pad])
        with self.lock:
            segments, _ = self.model.transcribe(
                audio, language="en", beam_size=self.beam_size, initial_prompt=prompt or None,
                condition_on_previous_text=False, temperature=0.0, vad_filter=self.vad,
                without_timestamps=True)
            texts = [s.text.strip() for s in segments
                     if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        return " ".join(t for t in texts if t).strip()


# ------------------------------------------------------------------ speaking

class Speaker:
    """Piper voices, loaded once. A voice is 'en_GB-cori-medium' or, for a
    multi-speaker model, 'en_US-libritts_r-medium#123'."""

    def __init__(self, voices_dir: Path = DEFAULT_VOICES_DIR):
        self.voices_dir = Path(voices_dir)
        self._voices = {}
        self.lock = threading.Lock()

    def _load(self, name: str):
        try:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
        except Exception:
            pass
        from piper import PiperVoice
        model = name.split("#")[0]
        if model not in self._voices:
            path = self.voices_dir / f"{model}.onnx"
            if not path.exists():
                raise FileNotFoundError(
                    f"voice {model} not found in {self.voices_dir}; run: python -m phraseology download")
            self._voices[model] = PiperVoice.load(path)
        return self._voices[model]

    def preload(self, *names: str):
        for n in names:
            self._load(n)

    def wav(self, text: str, voice: str, length_scale: float = 1.0) -> bytes:
        from piper import SynthesisConfig
        speaker_id = int(voice.split("#")[1]) if "#" in voice else None
        with self.lock:
            v = self._load(voice)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                v.synthesize_wav(text, w, syn_config=SynthesisConfig(
                    speaker_id=speaker_id, length_scale=length_scale))
        return buf.getvalue()

    def array(self, text: str, voice: str, length_scale: float = 1.0) -> np.ndarray:
        """Synthesized speech resampled to 16 kHz (used by the tests)."""
        x, _ = wav_to_array(self.wav(text, voice, length_scale))
        return x
