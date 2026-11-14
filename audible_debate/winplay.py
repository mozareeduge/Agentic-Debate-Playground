"""Windows playback with a REAL pause: winmm MCI through ctypes (stdlib only).

MciPlayer keeps ClockPlayer's contract (blocking play(), still_valid() checked every tick,
refuses over-ceiling durations, returns the position on stop) and drives the sound device:
pause/resume/stop act on the actual output, not only on the clock.

All MCI commands run on one dedicated thread (MCI aliases are tied to the thread that opened them).
The position comes from the device (`status position`) so the clock cannot drift from the sound.
Every stop records `last_stop_latency_ms`: from the stop request to MCI `stop` returning.
"""

from __future__ import annotations

import queue
import sys
import threading
import time

from .audio import ClockPlayer, PlaybackInterrupted


class MciError(RuntimeError):
    pass


class _MciThread:
    def __init__(self):
        import ctypes
        self._send = ctypes.windll.winmm.mciSendStringW
        self._buf = ctypes.create_unicode_buffer
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True, name="mci").start()

    def _loop(self):
        while True:
            cmd, box, done = self._q.get()
            buf = self._buf(256)
            rc = self._send(cmd, buf, 255, None)
            box.append((rc, buf.value))
            done.set()

    def call(self, cmd: str, timeout_s: float = 5.0) -> str:
        box, done = [], threading.Event()
        self._q.put((cmd, box, done))
        if not done.wait(timeout_s):
            raise MciError(f"MCI timeout: {cmd}")
        rc, val = box[0]
        if rc != 0:
            raise MciError(f"MCI error {rc}: {cmd}")
        return val


class MciPlayer(ClockPlayer):
    available = sys.platform == "win32"

    def __init__(self, tick_s: float = 0.02):
        if not self.available:
            raise RuntimeError("MciPlayer needs Windows")
        super().__init__(time_scale=1.0, tick_s=tick_s)
        self._mci = _MciThread()
        self._alias = None
        self._n = 0
        self.last_stop_latency_ms: float | None = None

    def _device_start(self, path: str):
        self._n += 1
        alias = f"debate{self._n}"
        self._mci.call(f'open "{path}" type waveaudio alias {alias}')
        self._mci.call(f"set {alias} time format milliseconds")
        self._mci.call(f"play {alias}")
        self._alias = alias

    def _device_pause(self):
        if self._alias:
            try:
                self._mci.call(f"pause {self._alias}")
            except MciError:
                pass

    def _device_resume(self):
        if self._alias:
            try:
                self._mci.call(f"resume {self._alias}")
            except MciError:
                pass

    def _device_stop(self):
        alias, self._alias = self._alias, None
        if alias:
            t0 = time.perf_counter()
            try:
                self._mci.call(f"stop {alias}")
            except MciError:
                pass
            self.last_stop_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            try:
                self._mci.call(f"close {alias}")
            except MciError:
                pass

    def device_position_s(self) -> float | None:
        if not self._alias:
            return None
        try:
            return int(self._mci.call(f"status {self._alias} position") or 0) / 1000.0
        except (MciError, ValueError):
            return None

    def device_mode(self) -> str | None:
        if not self._alias:
            return None
        try:
            return self._mci.call(f"status {self._alias} mode")
        except MciError:
            return None

    def play(self, path: str, duration_s: float, ceiling_s: float, still_valid=lambda: True) -> dict:
        if duration_s > ceiling_s + 1e-6:
            raise ValueError(f"refusing playback: {duration_s:.2f}s exceeds ceiling {ceiling_s:.2f}s")
        self.position_s = 0.0
        if self._stopped.is_set() or not still_valid():
            raise PlaybackInterrupted(0.0)
        paused_total, last = 0.0, time.monotonic()
        self._device_start(path)
        try:
            while True:
                if self._stopped.is_set() or not still_valid():
                    raise PlaybackInterrupted(self.position_s)
                now = time.monotonic()
                if self._paused.is_set():
                    paused_total += now - last
                    if self.device_mode() == "playing":   # a pause that raced the start: enforce it
                        self._device_pause()
                last = now
                pos = self.device_position_s()
                if pos is not None:
                    self.position_s = min(duration_s, pos)
                mode = self.device_mode()
                if mode in ("stopped", None) and not self._paused.is_set() and self.position_s >= duration_s - 0.05:
                    break
                if mode == "stopped" and not self._paused.is_set():
                    self.position_s = duration_s
                    break
                time.sleep(self.tick_s)
        finally:
            if not self._stopped.is_set():
                self._device_stop()
            elif self._alias:
                self._device_stop()
        return {"played_s": self.position_s, "paused_s": paused_total}
