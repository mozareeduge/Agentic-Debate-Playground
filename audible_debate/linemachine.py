"""Voices from the owner's Line Machine (local voice server, default http://127.0.0.1:4795).

The debate only CALLS the Line Machine over HTTP. It never edits its files and never sends
`save` (that would write packet files into the Line Machine's own folder).

  GET  /api/voices   -> [{"id", "fa" (label), "g", "lang", ...}]   local Piper fa, Windows en, cloud ids "gemini:Kore"...
  POST /api/speak    {"fa": text, "voice": id, "texture": "dry"} -> audio/wav

Duration is always measured from the WAV that comes back. A reply that is not RIFF/WAVE is refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .audio import Rendered, TTSError, analyze_wav, is_wav_bytes, measure_wav, sha256_file

DEFAULT_URL = os.environ.get("DEBATE_LINE_MACHINE_URL", "http://127.0.0.1:4795")
LINE_MACHINE_DIR = Path(os.environ.get(
    "DEBATE_LINE_MACHINE_DIR",
    str(Path.home() / "Documents" / "01 Art & Literature" / "Digital art" / "anja-line-machine")))


def _get(url: str, timeout_s: float):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))


def list_voices(base_url: str = DEFAULT_URL, timeout_s: float = 3.0) -> dict:
    try:
        voices = _get(base_url.rstrip("/") + "/api/voices", timeout_s)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"status": "down", "reason": str(getattr(e, "reason", e))[:120], "voices": [],
                "action": "Press 'Start voices' (starts the Line Machine in the background, about 30-60 s)."}
    out = []
    for v in voices:
        vid = v.get("id", "")
        cloud = ":" in vid
        out.append({"id": vid, "label": v.get("fa") or vid, "gender": v.get("g"), "lang": v.get("lang", "fa"),
                    "kind": "cloud" if cloud else ("derived" if v.get("derived") else "local")})
    return {"status": "up", "voices": out}


def start_line_machine(base_url: str = DEFAULT_URL) -> dict:
    """Start the Line Machine voice server hidden (no window, no browser, no sound). Idempotent."""
    if list_voices(base_url, 1.5)["status"] == "up":
        return {"started": False, "status": "already up"}
    d = LINE_MACHINE_DIR
    py = d / ".venv" / "Scripts" / "pythonw.exe"
    if not py.exists():
        py = d / ".venv" / "Scripts" / "python.exe"
    if not (d / "app.py").exists() or not py.exists():
        return {"started": False, "status": "not found", "reason": f"Line Machine not found at {d}"}
    (d / "log").mkdir(exist_ok=True)
    log = open(d / "log" / "server.log", "ab")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    env = dict(os.environ, PYTHONUTF8="1")
    subprocess.Popen([str(py), "-u", "app.py"], cwd=str(d), stdout=log, stderr=log, env=env, creationflags=flags)
    return {"started": True, "status": "starting", "note": "voices warm up for about 30-60 s"}


class LineMachineTTS:
    name = "line-machine"

    def __init__(self, voice: str, base_url: str = DEFAULT_URL, texture: str = "dry", timeout_s: float = 120.0):
        self.voice, self.base_url, self.texture, self.timeout_s = voice, base_url.rstrip("/"), texture, timeout_s
        self.version = "api/speak"

    def describe(self) -> dict:
        return {"engine": self.name, "voice": self.voice, "texture": self.texture, "url": self.base_url}

    def synthesize(self, text: str, out_path: Path, **_) -> Rendered:
        t0 = time.monotonic()
        body = json.dumps({"fa": text, "voice": self.voice, "texture": self.texture}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/api/speak", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            raise TTSError(f"Line Machine HTTP {e.code} for voice {self.voice}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TTSError(f"Line Machine unreachable ({self.base_url}): {e}") from e
        if not is_wav_bytes(data):
            raise TTSError(f"Line Machine voice {self.voice} returned {ctype or 'unknown'} ({len(data)} bytes), "
                           f"not WAV; refused")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        dur, rate, ch = measure_wav(out_path)
        return Rendered(str(out_path), sha256_file(out_path), dur, rate, ch, self.name, self.version,
                        {"voice": self.voice, "texture": self.texture}, time.monotonic() - t0,
                        voice=self.voice, analysis=analyze_wav(out_path))


def check_voice(voice: str, out_path: Path, text: str, base_url: str = DEFAULT_URL) -> dict:
    """Silent check (no playback): synthesize one line, report WAV facts and a measured speaking rate."""
    try:
        r = LineMachineTTS(voice, base_url).synthesize(text, out_path)
    except TTSError as e:
        return {"voice": voice, "ok": False, "reason": str(e)}
    words = len(text.split())
    # the Line Machine adds a short lead-in of silence; subtract leading quiet from the rate estimate
    speech_s = max(0.5, r.duration_s - _leading_silence_s(out_path))
    return {"voice": voice, "ok": bool(r.analysis and r.analysis["speech_like"]), "duration_s": round(r.duration_s, 2),
            "synth_s": round(r.synth_time_s, 2), "sample_rate": r.sample_rate, "words_per_second": round(words / speech_s, 2),
            "analysis": r.analysis}


def _leading_silence_s(path: Path) -> float:
    import array
    import wave
    with wave.open(str(path), "rb") as w:
        rate, n = w.getframerate(), w.getnframes()
        a = array.array("h", w.readframes(min(n, rate * 3)))
    for i, v in enumerate(a):
        if abs(v) > 600:
            return i / rate
    return 0.0
