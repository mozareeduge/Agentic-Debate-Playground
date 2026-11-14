"""Local control page: http://127.0.0.1:4797/ (stdlib only, bound to this computer).

  python -m audible_debate serve [--port 4797] [--silent]

The page sends every action here; the debate (models, voices, playback) runs in this process.
Playback is server-side through Windows MCI, so closing the browser tab does NOT stop the sound:
press Stop (or Stop server) on the page.

POST requests must carry the header `X-Debate: 1` (other web pages cannot send it without a
CORS preflight, which this server never grants), so a random site cannot drive the debate.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import fsm
from .audio import ClockPlayer, SpeechlessTTS
from .controller import Controller, SessionConfig
from .knowledge import KnowledgeProfile
from .linemachine import DEFAULT_URL as LM_URL, LineMachineTTS, check_voice, list_voices, start_line_machine
from .llm import MockAdapter, OpenAICompatAdapter, quick_test
from .personas import CUE_TECHNIQUES
from .sources import EFFORTS, discover_all, get_source, load_sources

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SESSIONS = ROOT / "sessions"
CHECKS = ROOT / "sessions" / "_voice_checks"
MOCK_SOURCE = {"id": "mock", "label": "Rehearsal script (no model, offline)", "status": "ok", "local": True,
               "needs_key": False, "has_key": False, "note": "Deterministic test text. Labelled MOCK in every record.",
               "models": [{"id": "mock-deterministic-v1", "why_free": "offline script", "router": False}]}
PLACEHOLDER_VOICE = {"id": "placeholder", "label": "Silent placeholder (no voice)", "lang": "any", "kind": "test"}
CHECK_LINES = {"en": "Under what conditions would we say that a machine has changed its mind?",
               "fa": "در چه شرایطی می‌گوییم که یک ماشین نظرش را تغییر داده است؟"}


def _player(mode: str):
    if mode == "audible":
        if sys.platform != "win32":
            raise ValueError("audible playback in this version needs Windows (MCI)")
        from .winplay import MciPlayer
        return MciPlayer()
    return ClockPlayer(time_scale=1.0)


class App:
    def __init__(self, default_mode: str = "audible"):
        self.default_mode = default_mode
        self.ctl: Controller | None = None
        self.lock = threading.RLock()
        self._sources_cache: tuple[float, list] | None = None
        self.voice_checks: dict = {}
        self.shutdown = threading.Event()
        threading.Thread(target=self._autosave, daemon=True).start()

    # ---------------------------------------------------------------- inventory
    def sources(self, refresh: bool = False) -> list:
        if refresh or not self._sources_cache or time.monotonic() - self._sources_cache[0] > 60:
            self._sources_cache = (time.monotonic(), discover_all(timeout_s=4.0))
        return [MOCK_SOURCE] + self._sources_cache[1]

    def live_models(self, refresh: bool = False) -> dict:
        """Ask every listed free model once (in parallel, no retries) so the page can mark which answer now.
        Cached for 5 minutes: free models change hour to hour, but not second to second."""
        from concurrent.futures import ThreadPoolExecutor
        cached = getattr(self, "_live_cache", None)
        if cached and not refresh and time.monotonic() - cached[0] < 300:
            return cached[1]
        srcs = {s.id: s for s in load_sources()}
        # paid accounts (Claude, Antigravity) are never swept: each probe would spend his limits
        jobs = [(s["id"], m["id"]) for s in self.sources(refresh) if s["id"] in srcs and s.get("status") == "ok"
                and srcs[s["id"]].probe and srcs[s["id"]].kind != "account" for m in s.get("models", [])]

        def probe(job):
            sid, mid = job
            try:
                r = quick_test(OpenAICompatAdapter.from_source(srcs[sid], mid, effort="none"), timeout_s=25.0)
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "seconds": 0.0, "note": str(e)[:160]}
            return sid, mid, r

        results: dict = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for sid, mid, r in ex.map(probe, jobs):
                results.setdefault(sid, {})[mid] = {"ok": bool(r.get("ok")), "seconds": r.get("seconds"),
                                                    "note": r.get("note", "")}
        out = {"checked_at": time.strftime("%H:%M"), "results": results,
               "live": sum(1 for v in results.values() for r in v.values() if r["ok"]), "total": len(jobs)}
        self._live_cache = (time.monotonic(), out)
        return out

    def voices(self) -> dict:
        lv = list_voices(LM_URL)
        lv["voices"] = lv["voices"] + [PLACEHOLDER_VOICE]
        lv["checks"] = self.voice_checks
        return lv

    def health(self, refresh: bool = False) -> dict:
        srcs = self.sources(refresh)
        lv = list_voices(LM_URL, 2.0)
        return {"sources": [{k: s.get(k) for k in ("id", "label", "status", "action", "reason", "key_env", "note")}
                            | {"models": len(s.get("models", []))} for s in srcs],
                "voices": {"status": lv["status"], "count": len(lv["voices"]), "action": lv.get("action"),
                           "url": LM_URL},
                "player": {"audible_available": sys.platform == "win32", "default_mode": self.default_mode},
                "server": {"pid": os.getpid(), "sessions_dir": str(SESSIONS)}}

    def check(self, voice: str, language: str) -> dict:
        if voice == "placeholder":
            return {"voice": voice, "ok": True, "note": "silent placeholder"}
        CHECKS.mkdir(parents=True, exist_ok=True)
        text = CHECK_LINES.get(language, CHECK_LINES["en"])
        res = check_voice(voice, CHECKS / f"{voice.replace(':', '_')}.wav", text, LM_URL)
        self.voice_checks[voice] = res
        return res

    # ---------------------------------------------------------------- session
    def _llm(self, perf: dict):
        if perf["source"] == "mock":
            return MockAdapter()
        src = get_source(perf["source"], load_sources())
        if src.needs_key() and not src.api_key():
            raise ValueError(f"{src.label} needs its key ({src.key_env}); choose another source or set the key")
        effort = perf.get("effort", "none")
        if effort not in EFFORTS:
            raise ValueError(f"effort must be one of {EFFORTS}")
        model = perf["model"]
        if src.effort_in_model:
            # Antigravity: effort is the model-name suffix; the API gets no reasoning_effort
            base = re.sub(r"-(x?high|medium|low)$", "", model)
            want = f"{base}-{'low' if effort == 'none' else effort}"
            listed = {m["id"] for s in self.sources() if s["id"] == src.id for m in s.get("models", [])}
            model = want if (not listed or want in listed) else model
            primary = OpenAICompatAdapter.from_source(src, model, effort="none")
            perf["model"], perf["effort_note"] = model, f"effort {effort} sent as model name {model}"
        else:
            primary = OpenAICompatAdapter.from_source(src, model, effort=effort)
        primary.min_timeout_s = float(src.timeout_s or 0)
        if not perf.get("backup", True):
            return primary
        # backups: models that answered the last live check, fastest first (same source first)
        live = self.live_models().get("results", {})   # cached 5 min; runs once if the page never asked
        srcs = {s.id: s for s in load_sources()}
        ranked = sorted(((sid, mid, r["seconds"]) for sid, ms in live.items() for mid, r in ms.items()
                         if r["ok"] and mid != perf["model"] and sid in srcs and srcs[sid].kind != "account"),
                        key=lambda x: (x[0] != src.id, x[2]))[:2]
        if not ranked:
            return primary
        from .llm import BackupAdapter
        return BackupAdapter(primary, [OpenAICompatAdapter.from_source(srcs[sid], mid, effort=effort)
                                       for sid, mid, _ in ranked])

    @staticmethod
    def _tts(perf: dict):
        if perf.get("voice", "placeholder") == "placeholder":
            return SpeechlessTTS(22050)
        return LineMachineTTS(perf["voice"], LM_URL)

    def create(self, body: dict) -> dict:
        with self.lock:
            if self.ctl and self.ctl.state in (fsm.RUNNING, fsm.PAUSED):
                raise ValueError("a debate is running; press Stop first")
            if self.ctl and self.ctl.state != fsm.CLOSED:
                self.ctl.quit()
            perfs = body.get("performers") or {}
            for k in ("A", "B"):
                if k not in perfs or not perfs[k].get("source") or not perfs[k].get("model"):
                    raise ValueError(f"performer {k}: choose a source and a model")
                chk = self.voice_checks.get(perfs[k].get("voice"))
                if chk and chk.get("words_per_second"):
                    perfs[k]["wps"] = chk["words_per_second"]
            mode = body.get("mode") or self.default_mode
            llm = {k: self._llm(perfs[k]) for k in ("A", "B")}
            tts = {k: self._tts(perfs[k]) for k in ("A", "B")}
            cfg = SessionConfig(
                question=str(body.get("question") or "").strip(),
                turns=int(body.get("turns", 6)), max_turn_seconds=float(body.get("max_turn_seconds", 75)),
                gap_seconds=float(body.get("gap_seconds", 1.5)), lookahead=int(body.get("lookahead", 3)),
                preroll_min=int(body.get("preroll_min", 2)), max_preroll_s=float(body.get("max_preroll_s", 90)),
                language=body.get("language", "en"), llm_timeout_s=float(body.get("llm_timeout_s", 60)),
                performers={k: {kk: perfs[k].get(kk) for kk in ("source", "model", "effort", "voice", "wps", "backup")}
                            for k in ("A", "B")},
                knowledge={"A": KnowledgeProfile("none"), "B": KnowledgeProfile("none")})
            self.ctl = Controller(llm, tts, _player(mode), sessions_root=SESSIONS)
            self.ctl.configure(cfg)
            self.ctl.store.write_json("mode.json", {"mode": mode, "note": "audible = Windows speakers via MCI; "
                                                    "silent = real files on a silent clock"})
            return self.status()

    def act(self, body: dict) -> dict:
        with self.lock:
            ctl = self.ctl
            if ctl is None:
                raise ValueError("no session yet: set up the performers and press Start")
            a = body.get("action")
            if a == "cue":
                ctl.cue(body.get("technique", ""), body.get("note", ""))
            elif a == "hold":
                ctl.hold(float(body.get("seconds", 3)))
            elif a == "save":
                ctl.save()
            elif a in ("start", "pause", "resume", "stop"):
                getattr(ctl, a)()
            else:
                raise ValueError(f"unknown action {a!r}")
            return self.status()

    def status(self) -> dict:
        if self.ctl is None:
            return {"state": "none", "allowed": [], "turns": [], "cues": sorted(CUE_TECHNIQUES)}
        st = self.ctl.status()
        st["cues"] = sorted(CUE_TECHNIQUES)
        st["mode"] = "audible" if type(self.ctl.player).__name__ == "MciPlayer" else "silent"
        st["config"] = self.ctl.config.to_dict() if self.ctl.config else None
        return st

    def _autosave(self):
        saved = None
        while not self.shutdown.is_set():
            ctl = self.ctl
            if ctl and ctl.state in (fsm.COMPLETED, fsm.STOPPED, fsm.ERROR) and saved != (id(ctl), ctl.state):
                try:
                    with self.lock:
                        ctl.save()
                    saved = (id(ctl), ctl.state)
                except Exception:  # noqa: BLE001 - a failed save is visible on the next status read
                    pass
            time.sleep(0.5)

    def stop_all(self):
        with self.lock:
            if self.ctl and self.ctl.state in (fsm.RUNNING, fsm.PAUSED):
                self.ctl.stop()
            if self.ctl and self.ctl.state != fsm.CLOSED:
                try:
                    self.ctl.save()
                except Exception:  # noqa: BLE001
                    pass
                self.ctl.quit()


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            sys.stderr.write("[web] " + fmt % a + "\n")

        def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                pass  # the page closed or reloaded before the answer arrived; nothing to do

        def do_GET(self):
            path = self.path.split("?")[0]
            q = self.path.partition("?")[2]
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api/health":
                    return self._send(200, app.health("refresh=1" in q))
                if path == "/api/sources":
                    return self._send(200, app.sources("refresh=1" in q))
                if path == "/api/voices":
                    return self._send(200, app.voices())
                if path == "/api/models/live":
                    return self._send(200, app.live_models("refresh=1" in q))
                if path == "/api/status":
                    return self._send(200, app.status())
                if path == "/api/efforts":
                    return self._send(200, list(EFFORTS))
                return self._send(404, {"error": "not found"})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self):
            if self.headers.get("X-Debate") != "1":
                return self._send(403, {"error": "missing X-Debate header"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            try:
                if self.path == "/api/session":
                    return self._send(200, app.create(body))
                if self.path == "/api/action":
                    return self._send(200, app.act(body))
                if self.path == "/api/voices/start":
                    return self._send(200, start_line_machine(LM_URL))
                if self.path == "/api/models/test":
                    if body.get("source") == "mock":
                        return self._send(200, {"ok": True, "seconds": 0.0, "note": "offline script"})
                    return self._send(200, quick_test(app._llm({"source": body.get("source"), "model": body.get("model"),
                                                                 "effort": "none"})))
                if self.path == "/api/voices/check":
                    return self._send(200, app.check(body.get("voice", ""), body.get("language", "en")))
                if self.path == "/api/shutdown":
                    app.stop_all()
                    self._send(200, {"ok": True, "note": "server stopped; double-click START-DEBATE to start again"})
                    threading.Thread(target=lambda: (time.sleep(0.3), app.shutdown.set()), daemon=True).start()
                    return
                return self._send(404, {"error": "not found"})
            except (ValueError, KeyError, fsm.IllegalTransition) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - reported to the page as an error, never hidden
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return H


def serve(port: int = 4797, default_mode: str = "audible"):
    if sys.stdout is None or sys.stderr is None:  # started hidden with pythonw: log to a file
        log = open(ROOT / ".debate-server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    app = App(default_mode=default_mode)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    print(f"Audible Debate -> http://127.0.0.1:{port}/  (mode default: {default_mode})", flush=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        app.shutdown.wait()
    except KeyboardInterrupt:
        pass
    app.stop_all()
    httpd.shutdown()
    return 0
