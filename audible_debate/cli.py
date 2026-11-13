"""Command-line view: one screen, explicit human cues.

  python -m audible_debate run --mock --speechless            # rehearsal, no model, no voice
  python -m audible_debate run --endpoint URL --model ID --speechless
  python -m audible_debate discover [URL ...]                  # read-only probe of local endpoints
  python -m audible_debate verify sessions/<id>                # replay events, check hashes/ceiling
  python -m audible_debate metrics sessions/<id>
  python -m audible_debate serve [--port 4797] [--silent]   # the control page (START-DEBATE.bat runs this)
  python -m audible_debate sources                         # live list of free model sources and models
  python -m audible_debate voices                          # Line Machine voices (if it is running)

During `run`, type a command and press Enter:
  start  pause  resume  stop  save  new  quit   (single letters: g p r s v n q)
  cue <technique> [note]   constraint on the NEXT turn only, e.g. `cue forced_compression`,
                           `cue fragment_seeding the river forgets its bed`   (`cue` alone lists techniques)
  hold <seconds>           designed extra silence before the next turn (logged as designed, not latency)
Nothing starts until the operator types `start` (or passes --start-now, which is logged as such).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from . import fsm
from .audio import ClockPlayer, PiperTTS, ProcessPlayer, SpeechlessTTS
from .controller import Controller, SessionConfig
from .knowledge import KnowledgeProfile, load_sources
from .llm import LLMError, MockAdapter, OpenAICompatAdapter, discover
from .metrics import run_metrics, verify_session
from .personas import CUE_TECHNIQUES

DEFAULT_QUESTION = "Can a machine change its mind?"
ALIASES = {"g": "start", "p": "pause", "r": "resume", "s": "stop", "v": "save", "n": "new", "q": "quit"}


def _print_event(e: dict):
    t = e["type"]
    if t == "action":
        msg = f"{e['action']}: {e['from_state']} -> {e['to_state']}" + (f"  ({e['error']})" if e.get("error") else "")
    elif t == "generate_end":
        msg = f"{e['turn_id']} text ready · {e['words']} words · move={e['move']} · {e['latency_s']}s · {e['model']}"
    elif t == "synth_end":
        msg = f"{e['turn_id']} audio {e['duration_s']}s · {e['engine']}"
    elif t == "play_begin":
        msg = f"{e['turn_id']} ▶ playing {e['duration_s']}s"
    elif t == "play_end":
        msg = f"{e['turn_id']} ■ played {e['played_s']}s (paused {e['paused_s']}s)"
    else:
        msg = t + " " + json.dumps({k: v for k, v in e.items() if k not in ("seq", "t_utc", "t_mono", "epoch", "state", "type")})
    print(f"[{e['seq']:04d} {e['state']:>9} e{e['epoch']}] {msg}", flush=True)


def _knowledge(path):
    return load_sources(path) if path else KnowledgeProfile("none")


def build_controller(a) -> tuple[Controller, SessionConfig]:
    if a.mock:
        llm = MockAdapter(latency_s=a.mock_latency)
    else:
        if not a.model:
            raise SystemExit("--model is required for a live run (see `discover`)")
        llm = OpenAICompatAdapter(a.endpoint, a.model, structured=not a.no_structured)
    if a.piper_model:
        tts = PiperTTS(a.piper_model)
    elif a.speechless:
        tts = SpeechlessTTS(a.sample_rate)
    else:
        raise SystemExit("choose a TTS: --speechless (placeholder audio) or --piper-model PATH")
    player = ProcessPlayer() if a.audible else ClockPlayer(time_scale=a.time_scale)
    ctl = Controller(llm, tts, player, sessions_root=a.sessions, on_event=_print_event)
    cfg = SessionConfig(question=a.question, turns=a.turns, max_turn_seconds=a.max_turn_seconds,
                        gap_seconds=a.gap, history_turns=a.history,
                        llm_params={"temperature": a.temperature, "max_tokens": a.max_tokens,
                                    **({"seed": a.seed} if a.seed is not None else {})},
                        llm_timeout_s=a.timeout, lookahead=a.lookahead, preroll_min=a.preroll,
                        knowledge={"A": _knowledge(a.sources_a), "B": _knowledge(a.sources_b)})
    return ctl, cfg


def cmd_run(a):
    ctl, cfg = build_controller(a)
    ctl.configure(cfg)
    print(f"session {ctl.store.session_id} · dir {ctl.store.dir}")
    print(f"question: {cfg.question}")
    print(f"ceiling {cfg.max_turn_seconds}s/turn · {cfg.turns} turns · knowledge A={cfg.knowledge['A'].mode} B={cfg.knowledge['B'].mode}")
    if ctl._labels()["any"]:
        print("LABEL: " + ctl._labels()["label"])
    if a.start_now:
        ctl.start(source="cli --start-now")
    else:
        print("ready. type `start` to begin.")
    done = threading.Event()

    def watcher():
        while not done.is_set():
            if ctl.state in (fsm.COMPLETED, fsm.ERROR) and a.start_now and a.exit_when_done:
                done.set()
                return
            done.wait(0.1)

    threading.Thread(target=watcher, daemon=True).start()
    while not done.is_set():
        if a.start_now and a.exit_when_done:
            if done.wait(0.2):
                break
            continue
        line = sys.stdin.readline()
        if not line:
            break
        parts = line.strip().split(maxsplit=2)
        cmd = ALIASES.get(parts[0].lower(), parts[0].lower()) if parts else ""
        if not cmd:
            print(f"state={ctl.state} phase={ctl.phase} allowed={fsm.operator_actions(ctl.state)}")
            continue
        try:
            if cmd == "save":
                m = ctl.save()
                print(f"saved {m['turns']} turns -> {ctl.store.dir}")
            elif cmd == "cue":
                if len(parts) < 2:
                    print("techniques: " + ", ".join(sorted(CUE_TECHNIQUES)))
                else:
                    ctl.cue(parts[1], parts[2] if len(parts) > 2 else "")
            elif cmd == "hold":
                ctl.hold(float(parts[1]) if len(parts) > 1 else 3.0)
            elif cmd in ("start", "pause", "resume", "stop", "new", "quit"):
                getattr(ctl, cmd)()
                if cmd == "new":
                    print(f"new session {ctl.store.session_id}; type `start`")
                if cmd == "quit":
                    break
            else:
                print(f"unknown command {cmd!r}; allowed: {fsm.operator_actions(ctl.state)}")
        except fsm.IllegalTransition as e:
            print(f"not allowed: {e}")
        except ValueError as e:
            print(f"rejected: {e}")
    if ctl.state != fsm.CLOSED:
        if ctl.state not in (fsm.IDLE,):
            ctl.save()
        ctl.quit()
    if ctl.last_error:
        print("last error: " + ctl.last_error)
    print(f"session dir: {ctl.store.dir}")
    return 0 if ctl.last_error is None else 2


def main(argv=None):
    p = argparse.ArgumentParser(prog="audible_debate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--question", default=DEFAULT_QUESTION)
    r.add_argument("--turns", type=int, default=6)
    r.add_argument("--max-turn-seconds", type=float, default=120.0)
    r.add_argument("--gap", type=float, default=1.5, help="silence between turns (s)")
    r.add_argument("--history", type=int, default=4)
    r.add_argument("--mock", action="store_true", help="deterministic mock performers (labelled)")
    r.add_argument("--mock-latency", type=float, default=0.0)
    r.add_argument("--endpoint", help="OpenAI-compatible base URL (or env DEBATE_LLM_ENDPOINT)")
    r.add_argument("--model")
    r.add_argument("--no-structured", action="store_true", help="omit response_format (schema still validated)")
    r.add_argument("--temperature", type=float, default=0.8)
    r.add_argument("--max-tokens", type=int, default=600)
    r.add_argument("--seed", type=int)
    r.add_argument("--timeout", type=float, default=90.0)
    r.add_argument("--speechless", action="store_true", help="silent placeholder WAVs (labelled)")
    r.add_argument("--piper-model")
    r.add_argument("--sample-rate", type=int, default=24000, help="placeholder WAV rate; match the host audio contract")
    r.add_argument("--audible", action="store_true", help="play through ffplay/aplay/afplay")
    r.add_argument("--time-scale", type=float, default=1.0, help="TEST CLOCK ONLY: <1 compresses wall time")
    r.add_argument("--sources-a", help="JSON excerpt file for performer A only")
    r.add_argument("--sources-b", help="JSON excerpt file for performer B only")
    r.add_argument("--sessions", default="sessions")
    r.add_argument("--start-now", action="store_true", help="operator pre-authorises Start (logged)")
    r.add_argument("--exit-when-done", action="store_true")
    r.add_argument("--lookahead", type=int, default=0, help="turns written ahead of what is heard (0 = serial v0)")
    r.add_argument("--preroll", type=int, default=2, help="voices ready before the first sound (pipeline)")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=4797)
    sv.add_argument("--silent", action="store_true", help="default to silent rehearsal (agents/tests)")
    sub.add_parser("sources")
    sub.add_parser("voices")
    d = sub.add_parser("discover")
    d.add_argument("endpoints", nargs="*", default=["http://localhost:1234", "http://127.0.0.1:1234"])
    v = sub.add_parser("verify")
    v.add_argument("session_dir")
    m = sub.add_parser("metrics")
    m.add_argument("session_dir")
    a = p.parse_args(argv)

    if a.cmd == "run":
        try:
            return cmd_run(a)
        except (LLMError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    if a.cmd == "serve":
        from .web import serve
        return serve(a.port, "silent" if a.silent else "audible")
    if a.cmd == "sources":
        from .sources import discover_all
        print(json.dumps(discover_all(), indent=2))
        return 0
    if a.cmd == "voices":
        from .linemachine import list_voices
        print(json.dumps(list_voices(), indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "discover":
        print(json.dumps(discover(a.endpoints), indent=2))
        return 0
    if a.cmd == "verify":
        res = verify_session(a.session_dir)
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    if a.cmd == "metrics":
        turns = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(a.session_dir, "turns").glob("*.json"))]
        print(json.dumps(run_metrics(turns), indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
