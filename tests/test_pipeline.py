"""v1 tests: text-ahead pipeline, per-performer brains, sources, Line Machine voices, Windows player.

All offline and silent: mock/scripted models, local fake HTTP servers, silent WAVs.
Run: python -m unittest discover -s tests -v
"""

import io
import json
import sys
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from audible_debate import fsm
from audible_debate.audio import ClockPlayer, Rendered, SpeechlessTTS, TTSError, analyze_wav
from audible_debate.controller import Controller, SessionConfig
from audible_debate.linemachine import LineMachineTTS, check_voice, list_voices
from audible_debate.llm import Generation, LLMError, MockAdapter, OpenAICompatAdapter
from audible_debate.metrics import run_metrics, verify_session
from audible_debate.sources import Source, discover_source, is_free

FAST = 0.002


def wait_for(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def reply_json(utterance, **kw):
    d = {"utterance": utterance, "move": "assert", "addresses": "", "position_shift": "none",
         "uncertainty": "low", "source_ids": [], "analogy": False}
    d.update(kw)
    return json.dumps(d)


class CountingLLM:
    """Scripted model that records when each request started."""
    provider = "scripted-test"

    def __init__(self, model="m", latency=0.0, text="A short sentence for the test."):
        self.model, self.latency, self.text = model, latency, text
        self.starts = []

    def generate(self, messages, params, timeout_s):
        self.starts.append(time.monotonic())
        time.sleep(self.latency)
        return Generation(raw=reply_json(self.text), provider=self.provider, model=self.model, params=params,
                          latency_s=self.latency)


class SlowTTS(SpeechlessTTS):
    def __init__(self, delay):
        super().__init__(16000)
        self.delay, self.calls = delay, 0

    def synthesize(self, text, out_path, words_per_second, **kw):
        self.calls += 1
        time.sleep(self.delay)
        return super().synthesize(text, out_path, words_per_second)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, llm=None, tts=None, time_scale=FAST, **cfg):
        ctl = Controller(llm or MockAdapter(), tts or SpeechlessTTS(16000), ClockPlayer(time_scale=time_scale),
                         sessions_root=self.root)
        base = dict(question="Can a machine change its mind?", turns=6, max_turn_seconds=60, gap_seconds=1.0,
                    lookahead=3, preroll_min=2, max_preroll_s=60)
        base.update(cfg)
        ctl.configure(SessionConfig(**base))
        return ctl


class TestPipeline(Base):
    def test_P01_pipeline_completes_alternating_and_verifies(self):
        ctl = self.make()
        ctl.start()
        self.assertEqual(ctl.wait(20), fsm.COMPLETED)
        self.assertEqual([t["speaker"] for t in ctl.turns], list("ABABAB"))
        # each turn answered the opponent's previous WRITTEN turn
        for prev, t in zip(ctl.turns, ctl.turns[1:]):
            self.assertIn(prev["turn_id"], t["prior_turn_ids"])
        ctl.save()
        ctl.quit()
        res = verify_session(ctl.store.dir)
        self.assertTrue(res["ok"], res["problems"])

    def test_P02_text_runs_ahead_of_voices(self):
        # slow voices, fast text: by the time turn 1 is heard, later turns are already written
        ctl = self.make(tts=SlowTTS(0.05), time_scale=0.02)
        ctl.start()
        self.assertTrue(wait_for(lambda: ctl.phase == "playing"))
        self.assertGreaterEqual(len(ctl.written), 2)
        ctl.stop()
        ctl.quit()

    def test_T17_stop_with_full_queue_no_plays_after_stop(self):
        ctl = self.make(time_scale=0.05)
        ctl.start()
        self.assertTrue(wait_for(lambda: ctl.phase == "playing" and ctl.player.position_s > 0.5))
        self.assertGreater(len(ctl.written), len(ctl.turns))  # turns are queued ahead
        ctl.stop()
        ctl.wait(5)
        heard = len(ctl.turns)
        time.sleep(0.4)
        self.assertEqual(len(ctl.turns), heard)  # nothing queued is ever heard after Stop
        self.assertEqual(ctl.state, fsm.STOPPED)
        kinds = [json.loads(l)["kind"] for l in (ctl.store.dir / "residue.jsonl").read_text().splitlines()]
        self.assertIn("generated_not_heard", kinds)
        self.assertIn("interrupted_turn", kinds)
        ctl.quit()

    def test_T18_pause_while_writer_ahead_starts_no_request(self):
        llm = CountingLLM(latency=0.05)
        ctl = self.make(llm=llm, time_scale=0.05)
        ctl.start()
        self.assertTrue(wait_for(lambda: ctl.phase == "playing"))
        ctl.pause()
        t_pause = time.monotonic()
        time.sleep(0.3)  # an in-flight request may finish; none may START
        self.assertFalse([s for s in llm.starts if s > t_pause + 0.06])
        p_requests = [r for r in ctl.request_log if r["paused"]]
        self.assertEqual(p_requests, [])
        pos = ctl.player.position_s
        time.sleep(0.2)
        self.assertAlmostEqual(ctl.player.position_s, pos, delta=0.05)
        ctl.resume()
        self.assertEqual(ctl.wait(20), fsm.COMPLETED)
        ctl.quit()

    def test_T19_reopened_app_never_autoplays(self):
        ctl = self.make(turns=2)
        self.assertEqual(ctl.state, fsm.READY)
        time.sleep(0.2)
        self.assertEqual(ctl.turns, [])
        self.assertIsNone(ctl._worker)  # nothing runs until the operator presses Start
        ctl.quit()
        ctl2 = self.make(turns=2)  # "restart": a fresh controller on the same sessions root
        self.assertEqual(ctl2.state, fsm.READY)
        self.assertIsNone(ctl2._worker)
        ctl2.quit()

    def test_T20_adaptive_preroll_waits_for_deficit_bigger_than_lookahead(self):
        # production 0.25 s per turn vs heard ~1 audible s * 0.02 = tiny: a large deficit; preroll must
        # buffer past lookahead=1 (all turns) instead of starting after 2 and running dry
        llm = CountingLLM(latency=0.15)
        ctl = self.make(llm=llm, turns=6, lookahead=1, preroll_min=2, time_scale=0.02)
        ctl.start()
        self.assertEqual(ctl.wait(30), fsm.COMPLETED)
        self.assertFalse(ctl.preroll["capped"])
        self.assertGreater(ctl.preroll["ready"], 2)  # it waited for more than the minimum
        waits = [t["undesigned_wait_s"] for t in ctl.turns[1:]]
        self.assertLess(max(waits), 0.05, waits)  # no underrun after the pre-roll
        ctl.quit()

    def test_T21_underrun_logged_as_undesigned_wait(self):
        llm = CountingLLM(latency=0.2)
        ctl = self.make(llm=llm, turns=4, preroll_min=1, max_preroll_s=0.01, time_scale=0.01)
        ctl.start()
        self.assertEqual(ctl.wait(30), fsm.COMPLETED)
        self.assertTrue(ctl.preroll["capped"])
        events = (ctl.store.dir / "events.jsonl").read_text()
        self.assertIn('"preroll_capped"', events)
        self.assertIn('"underrun"', events)
        m = run_metrics(ctl.turns)
        self.assertGreater(m["beat"]["max_underrun_after_first_s"], 0.05)
        ctl.quit()

    def test_T24_per_performer_brains_and_voices_recorded(self):
        a, b = CountingLLM(model="model-a"), CountingLLM(model="model-b")
        ctl = self.make(llm={"A": a, "B": b}, turns=4,
                        performers={"A": {"source": "s1", "model": "model-a", "effort": "low"},
                                    "B": {"source": "s2", "model": "model-b", "effort": "high"}})
        ctl.start()
        self.assertEqual(ctl.wait(20), fsm.COMPLETED)
        self.assertEqual(len(a.starts), 2)
        self.assertEqual(len(b.starts), 2)
        self.assertEqual([t["served_model"] for t in ctl.turns], ["model-a", "model-b"] * 2)
        cfg = json.loads((ctl.store.dir / "config.json").read_text())
        self.assertEqual(cfg["config"]["performers"]["B"]["effort"], "high")
        self.assertEqual(cfg["adapters"]["llm"]["A"]["model"], "model-a")
        self.assertFalse(any(t["served_mismatch"] for t in ctl.turns))
        ctl.quit()

    def test_P03_error_in_writer_fails_run_no_substitution(self):
        class Broken(CountingLLM):
            def generate(self, *a, **k):
                raise LLMError("HTTP 503 (after 3 attempt(s) on m; no other model is used)")
        ctl = self.make(llm=Broken())
        ctl.start()
        self.assertEqual(ctl.wait(10), fsm.ERROR)
        self.assertIn("no other model", ctl.last_error)
        ctl.quit()

    def test_P04_cue_lands_on_next_written_turn(self):
        llm = CountingLLM(latency=0.02)
        seen = []
        orig = llm.generate
        llm.generate = lambda m, p, t: (seen.append(m[1]["content"]), orig(m, p, t))[1]
        ctl = self.make(llm=llm, turns=3, time_scale=0.05)
        ctl.cue("forced_compression")
        ctl.start()
        self.assertEqual(ctl.wait(20), fsm.COMPLETED)
        self.assertIn("OPERATOR CUE", seen[0])
        self.assertNotIn("OPERATOR CUE", seen[1])
        ctl.quit()


# ---------------------------------------------------------------- fake HTTP servers
class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _wav_bytes(seconds=1.0, rate=16000, loud=True):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        import math
        frames = bytearray()
        for i in range(int(seconds * rate)):
            v = int(8000 * math.sin(2 * math.pi * 220 * i / rate)) if loud else 0
            frames += v.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class TestAdapters(Base):
    def test_T22_retry_same_model_then_fail_no_substitution(self):
        hits = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                hits.append(body["model"])
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"error":"rate"}')

        srv = _Server(H)
        try:
            a = OpenAICompatAdapter(srv.url, "free-model", effort="low", retries=2, backoff_s=(0.01, 0.01))
            with self.assertRaises(LLMError) as cm:
                a.generate([{"role": "user", "content": "x"}], {}, 5)
            self.assertEqual(hits, ["free-model"] * 3)  # same model every time
            self.assertIn("no other model", str(cm.exception))
        finally:
            srv.close()

    def test_P08_timeout_retried_once_only(self):
        hits = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                hits.append(1)
                self.rfile.read(int(self.headers["Content-Length"]))
                time.sleep(0.6)  # slower than the client timeout
                try:
                    self.send_response(200)
                    self.end_headers()
                except OSError:
                    pass

        srv = _Server(H)
        try:
            a = OpenAICompatAdapter(srv.url, "slow-model", retries=2, backoff_s=(0.01, 0.01))
            t0 = time.monotonic()
            with self.assertRaises(LLMError):
                a.generate([{"role": "user", "content": "x"}], {}, 0.3)
            self.assertEqual(len(hits), 2)  # first try + one retry, not three
            self.assertLess(time.monotonic() - t0, 2.0)
        finally:
            srv.close()

    def test_P05_effort_and_dropped_param_recorded(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                if "reasoning_effort" in body:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error":"reasoning_effort is not supported"}')
                    return
                out = {"model": "served/x", "choices": [{"message": {"content": reply_json("Hi.")}}]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(out).encode())

        srv = _Server(H)
        try:
            a = OpenAICompatAdapter(srv.url, "x", effort="high")
            g = a.generate([{"role": "user", "content": "x"}], {"max_tokens": 600}, 5)
            self.assertEqual(seen[0]["reasoning_effort"], "high")
            self.assertGreaterEqual(seen[0]["max_tokens"], 4000)
            self.assertNotIn("reasoning_effort", seen[1])
            self.assertEqual(g.usage["dropped_params"], ["reasoning_effort"])
            self.assertEqual(g.usage["chosen_model"], "x")
            self.assertEqual(g.model, "served/x")
        finally:
            srv.close()

    def test_T23_line_machine_wav_ok_and_non_wav_refused(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps([{"id": "en_david", "fa": "David", "g": "M", "lang": "en"},
                                             {"id": "gemini:Kore", "fa": "Kore", "g": "F", "lang": "fa"}]).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert "save" not in body  # never write packets into the Line Machine folder
                self.send_response(200)
                if body["voice"] == "mp3voice":
                    self.send_header("Content-Type", "audio/mpeg")
                    self.end_headers()
                    self.wfile.write(b"ID3" + b"\0" * 200)
                    return
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                self.wfile.write(_wav_bytes(1.2))

        srv = _Server(H)
        try:
            lv = list_voices(srv.url)
            self.assertEqual(lv["status"], "up")
            self.assertEqual([v["kind"] for v in lv["voices"]], ["local", "cloud"])
            r = LineMachineTTS("en_david", srv.url).synthesize("hello there", self.root / "a.wav")
            self.assertAlmostEqual(r.duration_s, 1.2, places=2)
            self.assertTrue(r.analysis["speech_like"])
            self.assertEqual(r.voice, "en_david")
            with self.assertRaises(TTSError):
                LineMachineTTS("mp3voice", srv.url).synthesize("hello", self.root / "b.wav")
            chk = check_voice("en_david", self.root / "c.wav", "one two three four", srv.url)
            self.assertTrue(chk["ok"])
            self.assertGreater(chk["words_per_second"], 0)
        finally:
            srv.close()
        self.assertEqual(list_voices("http://127.0.0.1:9", 0.5)["status"], "down")

    def test_P06_silence_is_not_speech_like(self):
        p = self.root / "s.wav"
        p.write_bytes(_wav_bytes(1.0, loud=False))
        self.assertFalse(analyze_wav(p)["speech_like"])

    def test_P07_sources_free_filter_and_keys(self):
        kilo = Source(id="k", label="k", base_url="http://x/v1", free={"suffix": [":free", "/free"]},
                      router_models=["kilo/kilo-auto/free"])
        self.assertTrue(is_free(kilo, {"id": "a/b:free"})[0])
        self.assertTrue(is_free(kilo, {"id": "kilo/kilo-auto/free"})[0])
        self.assertFalse(is_free(kilo, {"id": "a/paid-model"})[0])
        self.assertTrue(is_free(kilo, {"id": "a/p", "pricing": {"prompt": "0", "completion": "0"}})[0])
        keyfile = self.root / "env"
        keyfile.write_text("OTHER=1\nMY_KEY=secret-value\n")
        s = Source(id="r", label="r", base_url="http://127.0.0.1:9/v1", key_env="MY_KEY_NOT_SET_XYZ",
                   key_file=f"dotenv:{keyfile}#MY_KEY")
        self.assertEqual(s.api_key(), "secret-value")
        self.assertNotIn("secret-value", json.dumps(s.public()))
        down = discover_source(Source(id="d", label="d", base_url="http://127.0.0.1:9/v1"), 0.5)
        self.assertEqual(down["status"], "offline")
        nokey = discover_source(Source(id="n", label="n", base_url="http://127.0.0.1:9/v1",
                                       key_env="SURELY_NOT_SET_ABC"), 0.5)
        self.assertEqual(nokey["status"], "needs_key")
        self.assertIn("SURELY_NOT_SET_ABC", nokey["action"])


@unittest.skipUnless(sys.platform == "win32", "Windows MCI only")
class TestWindowsPlayerSilent(Base):
    """Drives the real Windows sound device with a SILENT WAV: nothing audible is produced."""

    def test_T25_mci_pause_freezes_and_stop_is_fast(self):
        from audible_debate.winplay import MciPlayer
        p = self.root / "silent.wav"
        p.write_bytes(_wav_bytes(3.0, loud=False))
        pl = MciPlayer()
        out = {}

        def run():
            try:
                out["facts"] = pl.play(str(p), 3.0, 30.0)
            except Exception as e:  # PlaybackInterrupted
                out["err"] = e

        th = threading.Thread(target=run)
        th.start()
        self.assertTrue(wait_for(lambda: pl.position_s > 0.3, 5))
        pl.pause()
        time.sleep(0.15)
        self.assertEqual(pl.device_mode(), "paused")
        pos = pl.device_position_s()
        time.sleep(0.4)
        self.assertAlmostEqual(pl.device_position_s(), pos, delta=0.03)  # the device really froze
        pl.resume()
        time.sleep(0.2)
        self.assertEqual(pl.device_mode(), "playing")
        pl.stop()
        th.join(3)
        self.assertIn("err", out)  # PlaybackInterrupted with the position
        self.assertIsNotNone(pl.last_stop_latency_ms)
        self.assertLess(pl.last_stop_latency_ms, 150)
        self.assertIsNone(pl.device_mode())  # closed: no device left playing

    def test_T26_mci_plays_to_end(self):
        from audible_debate.winplay import MciPlayer
        p = self.root / "silent1.wav"
        p.write_bytes(_wav_bytes(0.8, loud=False))
        pl = MciPlayer()
        t0 = time.monotonic()
        facts = pl.play(str(p), 0.8, 30.0)
        self.assertGreaterEqual(time.monotonic() - t0, 0.7)
        self.assertAlmostEqual(facts["played_s"], 0.8, delta=0.1)
        with self.assertRaises(ValueError):
            pl.play(str(p), 31.0, 30.0)


if __name__ == "__main__":
    unittest.main()
