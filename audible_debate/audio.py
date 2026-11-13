"""TTS engines, duration measurement and playback.

Durations are always measured from decoded WAV samples, never estimated from text.

Engines
  SpeechlessTTS  writes a silent WAV whose length follows the persona speaking rate.
                 Lets turn logic, ceilings and pause/stop be tested before any voice is installed.
                 Every record carries engine='speechless-placeholder'; it is never presented as speech.
  PiperTTS       calls the `piper` CLI if installed (UNVERIFIED in the build session: not installed there).

Players
  ClockPlayer    advances a playback position against a monotonic clock, honours pause/resume/stop,
                 and refuses to play past the turn ceiling. `time_scale` < 1 compresses wall time for
                 automated tests only; it is a test clock, never an audio speed-up.
  ProcessPlayer  plays through an external CLI player (ffplay/aplay/afplay) while running the same
                 clock; pause uses SIGSTOP/SIGCONT on POSIX. UNVERIFIED on Windows.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path


class TTSError(Exception):
    pass


@dataclass
class Rendered:
    path: str
    sha256: str
    duration_s: float
    sample_rate: int
    channels: int
    engine: str
    engine_version: str
    params: dict
    synth_time_s: float


def measure_wav(path: str | Path) -> tuple[float, int, int]:
    with wave.open(str(path), "rb") as w:
        frames, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
    return frames / float(rate), rate, ch


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _finish(path: Path, engine: str, version: str, params: dict, t0: float) -> Rendered:
    dur, rate, ch = measure_wav(path)
    return Rendered(str(path), sha256_file(path), dur, rate, ch, engine, version, params, time.monotonic() - t0)


class SpeechlessTTS:
    name = "speechless-placeholder"
    version = "1"

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate

    def synthesize(self, text: str, out_path: Path, words_per_second: float, **_) -> Rendered:
        t0 = time.monotonic()
        words = len(text.split())
        dur = words / words_per_second + 0.3 * text.count(".")  # sentence pauses
        n = int(dur * self.sample_rate)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(b"\x00\x00" * n)
        return _finish(out_path, self.name, self.version,
                       {"words_per_second": words_per_second, "sample_rate": self.sample_rate}, t0)


class PiperTTS:
    name = "piper"

    def __init__(self, model_path: str, binary: str = "piper"):
        self.binary = shutil.which(binary) or binary
        self.model_path = model_path
        if not shutil.which(self.binary) and not Path(self.binary).exists():
            raise TTSError(f"piper binary not found ({binary}); TTS unavailable — use --speechless")
        self.version = "unknown"

    def synthesize(self, text: str, out_path: Path, length_scale: float = 1.0, **_) -> Rendered:
        t0 = time.monotonic()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.binary, "--model", self.model_path, "--output_file", str(out_path), "--length_scale", str(length_scale)]
        r = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=120)
        if r.returncode != 0 or not out_path.exists():
            raise TTSError(f"piper failed: {r.stderr[-400:]!r}")
        return _finish(out_path, self.name, self.version,
                       {"model": self.model_path, "length_scale": length_scale}, t0)


class PlaybackInterrupted(Exception):
    pass


class ClockPlayer:
    """Blocking play() with thread-safe pause/resume/stop and a hard ceiling."""

    def __init__(self, time_scale: float = 1.0, tick_s: float = 0.02):
        self.time_scale = time_scale
        self.tick_s = tick_s
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self.position_s = 0.0

    # hooks for subclasses that drive a real device
    def _device_start(self, path: str): ...
    def _device_pause(self): ...
    def _device_resume(self): ...
    def _device_stop(self): ...

    def pause(self):
        with self._lock:
            if not self._paused.is_set():
                self._paused.set()
                self._device_pause()

    def resume(self):
        with self._lock:
            if self._paused.is_set():
                self._paused.clear()
                self._device_resume()

    def stop(self):
        with self._lock:
            self._stopped.set()
            self._paused.clear()
            self._device_stop()

    def reset(self):
        """Arm the player for a new run. Called only when a new worker starts."""
        with self._lock:
            self._stopped.clear()
            self._paused.clear()
            self.position_s = 0.0

    def play(self, path: str, duration_s: float, ceiling_s: float, still_valid=lambda: True) -> dict:
        """Play up to min(duration, ceiling). Returns timing facts. Raises PlaybackInterrupted on stop."""
        if duration_s > ceiling_s + 1e-6:
            raise ValueError(f"refusing playback: {duration_s:.2f}s exceeds ceiling {ceiling_s:.2f}s")
        self.position_s = 0.0
        if self._stopped.is_set() or not still_valid():
            raise PlaybackInterrupted(0.0)
        paused_total = 0.0
        self._device_start(path)
        last = time.monotonic()
        try:
            while self.position_s < duration_s:
                if self._stopped.is_set() or not still_valid():
                    raise PlaybackInterrupted(self.position_s)
                now = time.monotonic()
                delta = (now - last) / self.time_scale
                last = now
                if self._paused.is_set():
                    paused_total += delta
                else:
                    self.position_s = min(duration_s, self.position_s + delta)
                time.sleep(self.tick_s)
        finally:
            if self._stopped.is_set():
                self._device_stop()
        return {"played_s": self.position_s, "paused_s": paused_total}


class ProcessPlayer(ClockPlayer):
    CANDIDATES = (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["aplay", "-q"], ["afplay"])

    def __init__(self, **kw):
        super().__init__(time_scale=1.0, **kw)
        self.cmd = next((c for c in self.CANDIDATES if shutil.which(c[0])), None)
        if self.cmd is None:
            raise RuntimeError("no CLI audio player found (ffplay/aplay/afplay)")
        self.proc: subprocess.Popen | None = None

    def _device_start(self, path):
        self.proc = subprocess.Popen([*self.cmd, path])

    def _signal(self, sig):
        if self.proc and self.proc.poll() is None and hasattr(signal, "SIGSTOP"):
            os.kill(self.proc.pid, sig)

    def _device_pause(self):
        self._signal(getattr(signal, "SIGSTOP", None))

    def _device_resume(self):
        self._signal(getattr(signal, "SIGCONT", None))

    def _device_stop(self):
        if self.proc and self.proc.poll() is None:
            self._device_resume()
            self.proc.terminate()
