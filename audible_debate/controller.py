"""Debate controller: alternating turns under human lifecycle control.

Sequencing is strictly A -> text -> B -> text -> A ... Each speaker receives
the question, the opponent's completed last turn, and a bounded shared history.
Two schedules:
  serial   (lookahead=0, the v0 default) the next turn is generated only after the
           previous one has finished playing.
  pipeline (lookahead>=1, owner decision 2026-11-14, OM-907) the text runs ahead of the
           voices: a writer writes A, B, A ... as text, a voicer turns each text into a WAV
           in order, and the player starts only after an adaptive pre-roll, then plays turn
           after turn separated by the designed gap. Pause parks all three: no new model or
           voice request starts while paused; an in-flight result is held until Resume.

Concurrency contract
  * operator actions may arrive from any thread (CLI, UI)
  * every action that invalidates work bumps `epoch`; any generation, synthesis
    or playback that finishes under an older epoch is discarded and logged
  * pause parks the worker at the next checkpoint; an in-flight model request is
    allowed to return but its result is held until resume (or discarded on stop)

Timing ("on-beat")
  The inter-turn gap is the designed pulse. Operator `hold` adds designed silence.
  Time spent waiting for the model and the synthesizer is recorded separately as
  `undesigned_wait_s`, so a reviewer can tell designed silence from latency.
  A failure is reported as a failure, never relabelled as a designed gesture.

Residue
  Every output removed from the active route (invalid, over budget, trimmed,
  shortened for the ceiling, discarded after Stop) is kept in residue.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fsm
from .audio import ClockPlayer, PlaybackInterrupted, TTSError
from .knowledge import KnowledgeProfile
from .llm import LLMError
from .personas import PERSONAS, cue_instruction, system_prompt
from .schema import ReplyInvalid, drop_last_sentence, parse_reply, trim_to_words, word_count
from .store import SessionStore

MIN_CEILING_S, MAX_CEILING_S = 30.0, 120.0
LANGUAGES = {"en": "", "fa": "LANGUAGE: speak in Persian (Farsi), written in Persian script. Keep the JSON keys in English."}


@dataclass
class SessionConfig:
    question: str
    turns: int = 6
    max_turn_seconds: float = 120.0
    gap_seconds: float = 1.5
    history_turns: int = 4
    first_speaker: str = "A"
    llm_params: dict = field(default_factory=lambda: {"temperature": 0.8, "max_tokens": 600})
    llm_timeout_s: float = 90.0
    max_regenerations: int = 2
    word_budget_fraction: float = 0.85
    knowledge: dict = field(default_factory=lambda: {"A": KnowledgeProfile("none"), "B": KnowledgeProfile("none")})
    lookahead: int = 0            # 0 = serial v0; N = writer may run N turns ahead of what is heard
    preroll_min: int = 2          # voices ready before the first sound (pipeline only)
    max_preroll_s: float = 90.0   # start anyway after this long (logged as preroll_capped)
    language: str = "en"          # en | fa (instruction to the performers; voices are chosen separately)
    performers: dict = field(default_factory=dict)  # {"A": {"source","model","effort","voice","wps"}, "B": {...}}

    def validate(self):
        if not self.question.strip():
            raise ValueError("question is empty")
        if not (MIN_CEILING_S <= self.max_turn_seconds <= MAX_CEILING_S):
            raise ValueError(f"max_turn_seconds must be within {MIN_CEILING_S:.0f}–{MAX_CEILING_S:.0f}")
        if self.turns < 1:
            raise ValueError("turns must be >= 1")
        if self.first_speaker not in PERSONAS:
            raise ValueError("first_speaker must be A or B")
        if self.lookahead < 0 or self.lookahead > 10:
            raise ValueError("lookahead must be within 0-10")
        if self.preroll_min < 1:
            raise ValueError("preroll_min must be >= 1")
        if self.language not in LANGUAGES:
            raise ValueError(f"language must be one of {sorted(LANGUAGES)}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["knowledge"] = {k: v.to_dict() for k, v in self.knowledge.items()}
        return d


class Cancelled(Exception):
    pass


class Controller:
    def __init__(self, llm, tts, player: ClockPlayer | None = None, sessions_root: str | Path = "sessions",
                 on_event=None):
        # llm / tts: one adapter for both performers, or {"A": adapter, "B": adapter}
        self.llm, self.tts = llm, tts
        self.player = player or ClockPlayer()
        self.sessions_root = Path(sessions_root)
        self.on_event = on_event or (lambda e: None)
        self.state = fsm.IDLE
        self.epoch = 0
        self.config: SessionConfig | None = None
        self.store: SessionStore | None = None
        self.turns: list[dict] = []      # heard turns (playing / played / interrupted), in order
        self.written: list[dict] = []    # pipeline: turns whose text exists (heard or not yet)
        self.phase = ""
        self.stages = {"writer": "", "voicer": "", "player": ""}
        self.preroll: dict = {}
        self._lock = threading.RLock()
        self._resume = threading.Event()
        self._resume.set()
        self._worker: threading.Thread | None = None
        self.last_error: str | None = None
        self.pending_cue: dict | None = None
        self.pending_hold: float = 0.0
        self._paused_wall = 0.0
        self._pause_t: float | None = None
        self.request_log: list[dict] = []   # every model/voice request start, with the state at that moment

    # ------------------------------------------------------------ adapters per performer
    def llm_for(self, speaker: str):
        return self.llm[speaker] if isinstance(self.llm, dict) else self.llm

    def tts_for(self, speaker: str):
        return self.tts[speaker] if isinstance(self.tts, dict) else self.tts

    @staticmethod
    def _all(x):
        return list(x.values()) if isinstance(x, dict) else [x]

    # ------------------------------------------------------------ events
    def _emit(self, type_: str, **data):
        if self.store is None:
            return
        rec = self.store.event(type_, self.epoch, self.state, **data)
        self.on_event(rec)

    def _act(self, action: str, **data):
        """Apply an operator/system action through the FSM and log it."""
        with self._lock:
            new = fsm.transition(self.state, action)
            old = self.state
            self.state = new
            if action in fsm.EPOCH_BUMPING:
                self.epoch += 1
            self._emit("action", action=action, from_state=old, to_state=new, **data)
            return new

    # ------------------------------------------------------------ operator API
    def configure(self, config: SessionConfig, adapters: dict | None = None):
        config.validate()
        with self._lock:
            if self.state not in (fsm.IDLE, fsm.READY):
                raise fsm.IllegalTransition(self.state, "configure")
            self.config = config
            if self.store is None:
                self.store = SessionStore(self.sessions_root)
            snapshot = {
                "session_id": self.store.session_id,
                "config": config.to_dict(),
                "personas": {k: {**p.to_dict(), "system_prompt": system_prompt(p)} for k, p in PERSONAS.items()},
                "adapters": adapters or self.describe_adapters(),
            }
            self.store.write_json("config.json", snapshot)
            self._act("configure", config_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest())

    @staticmethod
    def _describe_llm(a) -> dict:
        if hasattr(a, "describe"):
            return a.describe()
        return {"provider": getattr(a, "provider", "?"), "model": getattr(a, "model", "?")}

    @staticmethod
    def _describe_tts(t) -> dict:
        if hasattr(t, "describe"):
            return t.describe()
        return {"engine": getattr(t, "name", "?"), "version": getattr(t, "version", "?")}

    def describe_adapters(self) -> dict:
        llm = (self._describe_llm(self.llm) if not isinstance(self.llm, dict)
               else {k: self._describe_llm(v) for k, v in self.llm.items()})
        tts = (self._describe_tts(self.tts) if not isinstance(self.tts, dict)
               else {k: self._describe_tts(v) for k, v in self.tts.items()})
        return {"llm": llm, "tts": tts,
                "player": {"class": type(self.player).__name__, "time_scale": getattr(self.player, "time_scale", 1.0)}}

    def start(self, source: str = "operator"):
        with self._lock:
            self._act("start", source=source)
            epoch = self.epoch
            self.player.reset()
            self._resume.set()
            target = self._run_pipeline if self.config.lookahead > 0 else self._run
            self._worker = threading.Thread(target=target, args=(epoch,), daemon=True, name="debate-worker")
            self._worker.start()

    def pause(self):
        with self._lock:
            self._act("pause", phase=self.phase, position_s=round(self.player.position_s, 3), stages=dict(self.stages))
            self._pause_t = time.monotonic()
            self._resume.clear()
            self.player.pause()

    def resume(self):
        with self._lock:
            self._act("resume", phase=self.phase, position_s=round(self.player.position_s, 3))
            if self._pause_t is not None:
                self._paused_wall += time.monotonic() - self._pause_t
                self._pause_t = None
            self.player.resume()
            self._resume.set()

    def stop(self):
        with self._lock:
            t0 = time.perf_counter()
            self._act("stop", phase=self.phase, position_s=round(self.player.position_s, 3), stages=dict(self.stages))
            self.player.stop()
            self._emit("stop_latency", ms=round((time.perf_counter() - t0) * 1000, 2),
                       device_ms=getattr(self.player, "last_stop_latency_ms", None))
            self._resume.set()  # unblock a paused worker so it can observe the new epoch and exit

    def cue(self, technique: str, note: str = ""):
        """Place a constraint on the NEXT turn still to be WRITTEN (replaces any unconsumed cue; both logged)."""
        instruction = cue_instruction(technique, note)
        with self._lock:
            replaced = self.pending_cue
            lands_on = self._next_write_index()
            self._act("cue", technique=technique, note=note, instruction=instruction,
                      replaced=replaced["technique"] if replaced else None, lands_on_turn_index=lands_on,
                      turns_until_heard=max(0, lands_on - len(self.turns)))
            self.pending_cue = {"technique": technique, "note": note, "instruction": instruction}

    def _next_write_index(self) -> int:
        if self.config and self.config.lookahead > 0:
            return len(self.written)
        return len(self.turns)

    def hold(self, seconds: float):
        """Add designed silence before the next HEARD turn (0-30 s; accumulates until consumed)."""
        if not (0 < seconds <= 30):
            raise ValueError("hold must be within (0, 30] seconds")
        with self._lock:
            self._act("hold", seconds=seconds)
            self.pending_hold = min(30.0, self.pending_hold + seconds)

    def _paused_total(self) -> float:
        extra = time.monotonic() - self._pause_t if self._pause_t is not None else 0.0
        return self._paused_wall + extra

    def save(self) -> dict:
        with self._lock:
            self._act("save")
            return self.store.snapshot(self.config.to_dict(), list(self.turns), self.state, self._labels())

    def new(self):
        """Close this session (nothing deleted) and open a fresh one with the same configuration."""
        with self._lock:
            self._act("new")
            self._join()
            self.store.close()
            self.store = SessionStore(self.sessions_root)
            self.turns, self.written, self.preroll = [], [], {}
            self.state = fsm.IDLE
            self.configure(self.config)

    def quit(self):
        with self._lock:
            if self.state == fsm.CLOSED:
                return
            self.player.stop()
            self._resume.set()
            self._act("quit")
        self._join()
        if self.store:
            self.store.close()

    def wait(self, timeout: float | None = None) -> str:
        if self._worker:
            self._worker.join(timeout)
        return self.state

    def _join(self):
        w = self._worker
        if w and w.is_alive() and w is not threading.current_thread():
            w.join(timeout=10)

    def _labels(self) -> dict:
        mock = any(getattr(a, "provider", "") == "mock" for a in self._all(self.llm))
        placeholder = any(getattr(t, "name", "") == "speechless-placeholder" for t in self._all(self.tts))
        silent_player = type(self.player).__name__ == "ClockPlayer"
        parts = []
        if mock:
            parts.append("text from the deterministic MOCK adapter, not a live model")
        if placeholder:
            parts.append("audio is SILENT PLACEHOLDER (speechless test mode), not synthesized speech")
        if silent_player and not placeholder:
            parts.append("SILENT REHEARSAL: real audio files, played on a silent clock (nothing was heard)")
        return {"any": bool(parts), "mock_llm": mock, "placeholder_audio": placeholder,
                "silent_rehearsal": silent_player, "label": "; ".join(parts)}

    # ------------------------------------------------------------ status (for the page)
    def status(self) -> dict:
        cfg = self.config
        pipe = bool(cfg and cfg.lookahead > 0)
        heard = len(self.turns)
        rows = []
        for t in (self.written if pipe else self.turns):
            r = t.get("reply") or {}
            rows.append({"turn_id": t["turn_id"], "speaker": t["speaker"], "status": t["status"],
                         "text": r.get("utterance") or t.get("original_utterance", ""),
                         "model": t.get("served_model") or t.get("model"),
                         "duration_s": t.get("duration_s"), "write_s": t.get("write_s"),
                         "voice_s": t.get("voice_s"), "undesigned_wait_s": t.get("undesigned_wait_s"),
                         "speech_like": (t.get("audio_analysis") or {}).get("speech_like"),
                         "flags": [f for f in ("trimmed", "served_mismatch", "context_shortened_after_reply") if t.get(f)]})
        return {"session_id": self.store.session_id if self.store else None,
                "session_dir": str(self.store.dir) if self.store else None,
                "state": self.state, "phase": self.phase, "stages": dict(self.stages),
                "allowed": fsm.operator_actions(self.state), "epoch": self.epoch,
                "turns_total": cfg.turns if cfg else 0,
                "written": len(self.written) if pipe else heard,
                "voiced": sum(1 for t in self.written if t.get("wav")) if pipe else heard,
                "heard": heard, "position_s": round(self.player.position_s, 2),
                "preroll": {k: v for k, v in self.preroll.items() if k != "started"},
                "pending_cue": self.pending_cue, "pending_hold": self.pending_hold,
                "last_error": self.last_error, "labels": self._labels(), "turns": rows}

    # ------------------------------------------------------------ worker helpers
    def _checkpoint(self, epoch: int, phase: str | None = None):
        if phase:
            self.phase = phase
        while True:
            if self.epoch != epoch:
                raise Cancelled()
            if self._resume.wait(timeout=0.05):
                if self.epoch != epoch:
                    raise Cancelled()
                return

    def _request_gate(self, epoch: int, kind: str = "request"):
        """Called right before any model/voice request starts: none starts while paused."""
        self._checkpoint(epoch)
        self.request_log.append({"kind": kind, "t": time.monotonic(), "state": self.state,
                                 "paused": not self._resume.is_set()})

    def _wait_gap(self, epoch: int, seconds: float):
        scale = getattr(self.player, "time_scale", 1.0)
        remaining = seconds
        last = time.monotonic()
        self.phase = "gap"
        self.stages["player"] = "gap"
        while remaining > 0:
            self._checkpoint(epoch)
            now = time.monotonic()
            remaining -= (now - last) / scale
            last = now
            time.sleep(min(0.02, max(remaining * scale, 0)))

    def _fail(self, epoch: int, e: Exception, crash: bool = False):
        self.last_error = f"CRASH {type(e).__name__}: {e}" if crash else f"{type(e).__name__}: {e}"
        with self._lock:
            if self.epoch == epoch and self.state in (fsm.RUNNING, fsm.PAUSED):
                self._act("fail", phase=self.phase, error=self.last_error)

    # ------------------------------------------------------------ serial schedule (v0)
    def _run(self, epoch: int):
        cfg = self.config
        order = [cfg.first_speaker, "B" if cfg.first_speaker == "A" else "A"]
        try:
            for i in range(cfg.turns):
                speaker = order[i % 2]
                with self._lock:
                    hold, self.pending_hold = self.pending_hold, 0.0
                designed = (cfg.gap_seconds if i > 0 else 0.0) + hold
                if designed > 0:
                    self._emit("silence", designed_s=designed, operator_hold_s=hold, before_turn=i + 1)
                    self._wait_gap(epoch, designed)
                turn = self._do_turn(epoch, i, speaker, designed)
                if turn["reply"]["move"] == "end":
                    self._emit("scene_end_requested", turn_id=turn["turn_id"], speaker=speaker)
                    break
            with self._lock:
                if self.epoch == epoch:
                    self._act("complete", turns=len(self.turns))
        except Cancelled:
            self._emit("worker_cancelled", phase=self.phase, worker_epoch=epoch)
        except (LLMError, TTSError, ReplyInvalid, ValueError) as e:
            self._fail(epoch, e)
        except Exception as e:  # crash is reported as a crash, never as an aesthetic gesture
            self._fail(epoch, e, crash=True)
            raise

    def _do_turn(self, epoch: int, index: int, speaker: str, designed_silence_s: float = 0.0) -> dict:
        t_start, p_start = time.monotonic(), self._paused_total()
        played = [t for t in self.turns if t["status"] == "played"]
        turn = self._write(epoch, index, speaker, played, designed_silence_s)
        self._voice(epoch, turn)
        self._checkpoint(epoch, "playing")
        turn["undesigned_wait_s"] = round((time.monotonic() - t_start) - (self._paused_total() - p_start), 4)
        return self._play(epoch, turn)

    # ------------------------------------------------------------ the three stages
    def _score_position(self, index: int) -> str:
        rounds = (self.config.turns + 1) // 2
        r = index // 2 + 1
        phase = "opening" if index < 2 else ("closing" if r == rounds else "middle")
        return f"ROUND {r} OF {rounds} ({phase}); turn {index + 1} of {self.config.turns}"

    def _wps(self, speaker: str) -> float:
        perf = (self.config.performers or {}).get(speaker) or {}
        return float(perf.get("wps") or PERSONAS[speaker].speaking_rate_wps)

    def _messages(self, index: int, speaker: str, cue: dict | None = None,
                  context: list[dict] | None = None) -> tuple[list[dict], list[str], int]:
        cfg = self.config
        persona = PERSONAS[speaker]
        kp: KnowledgeProfile = cfg.knowledge[speaker]
        budget = max(20, int(cfg.max_turn_seconds * self._wps(speaker) * cfg.word_budget_fraction))
        played = context if context is not None else [t for t in self.turns if t["status"] == "played"]
        history = played[-cfg.history_turns:]
        opp = next((t for t in reversed(played) if t["speaker"] != speaker), None)
        earlier = [t for t in history if t is not opp]
        hist_txt = "\n".join(f"[{t['turn_id']} {t['speaker']}] {t['reply']['utterance']}" for t in earlier)
        user = [f"QUESTION: {cfg.question}", f"TURN INDEX: {index}", f"YOU ARE: performer {speaker}",
                f"SCORE POSITION: {self._score_position(index)}", ""]
        if LANGUAGES.get(cfg.language):
            user += [LANGUAGES[cfg.language], ""]
        if hist_txt:
            user += ["EARLIER EXCHANGE (most recent last):", hist_txt, ""]
        if opp:
            user += [f"OPPONENT LAST TURN [{opp['turn_id']}]:", opp["reply"]["utterance"], ""]
        else:
            user += ["You open the exchange. There is no previous turn.", ""]
        user += [kp.prompt_block(), ""]
        if cue:
            user += [f"OPERATOR CUE: {cue['instruction']}", ""]
        user += [f"WORD BUDGET: at most {budget} words in the utterance "
                 f"(about {int(cfg.max_turn_seconds)} seconds of speech at most).",
                 "Reply with one JSON object with keys: utterance, move, addresses, position_shift, uncertainty, source_ids, analogy."]
        msgs = [{"role": "system", "content": system_prompt(persona)}, {"role": "user", "content": "\n".join(user)}]
        prior = [t["turn_id"] for t in history]
        return msgs, prior, budget

    def _generate(self, epoch: int, speaker: str, msgs, cfg):
        llm = self.llm_for(speaker)
        self._request_gate(epoch, f"llm:{speaker}")
        if getattr(llm, "supports_gate", False):
            return llm.generate(msgs, cfg.llm_params, cfg.llm_timeout_s,
                                gate=lambda: self._request_gate(epoch, f"llm-retry:{speaker}"))
        return llm.generate(msgs, cfg.llm_params, cfg.llm_timeout_s)

    def _write(self, epoch: int, index: int, speaker: str, context: list[dict],
               designed_silence_s: float = 0.0) -> dict:
        """Write one turn's text (validation, budget-driven regeneration, trimming)."""
        cfg = self.config
        persona = PERSONAS[speaker]
        kp: KnowledgeProfile = cfg.knowledge[speaker]
        turn_id = f"t{index + 1:02d}{speaker}"
        p_start, t0 = self._paused_total(), time.monotonic()
        with self._lock:
            cue, self.pending_cue = self.pending_cue, None
        msgs, prior_ids, budget = self._messages(index, speaker, cue, context)
        prompt_path = self.store.write_json(f"prompts/{turn_id}.json", msgs)
        perf = (cfg.performers or {}).get(speaker) or {}
        turn = {"turn_id": turn_id, "session_id": self.store.session_id, "index": index, "speaker": speaker,
                "persona": persona.name, "prior_turn_ids": prior_ids, "sources_offered": sorted(kp.ids),
                "knowledge_mode": kp.mode, "prompt_file": str(prompt_path.relative_to(self.store.dir)),
                "word_budget": budget, "regenerations": 0, "trimmed": False, "status": "generating",
                "invalid_outputs": [], "operator_cue": cue, "designed_silence_s": designed_silence_s,
                "score_position": self._score_position(index), "performer": perf}

        self._checkpoint(epoch, "generating")
        self.stages["writer"] = f"writing {turn_id}"
        self._emit("generate_begin", turn_id=turn_id, speaker=speaker, word_budget=budget)
        reply, gen = None, None
        for attempt in range(cfg.max_regenerations + 1):
            gen = self._generate(epoch, speaker, msgs, cfg)
            if self.epoch != epoch:
                self.store.residue(turn_id, "stale_generation", "generation returned after Stop/New/Quit", gen.raw,
                                   reopen="never replayed into a later session")
                self._emit("stale_generation_discarded", turn_id=turn_id)
                raise Cancelled()
            try:
                reply = parse_reply(gen.raw, kp.ids)
            except ReplyInvalid as e:
                turn["invalid_outputs"].append({"attempt": attempt, "error": str(e), "raw": gen.raw[:2000]})
                self.store.residue(turn_id, "invalid_output", f"attempt {attempt}: {e}", gen.raw)
                self._emit("reply_invalid", turn_id=turn_id, attempt=attempt, error=str(e))
                turn["regenerations"] = attempt + 1
                reply = None
                continue
            if word_count(reply.utterance) > budget and attempt < cfg.max_regenerations:
                self._emit("over_word_budget", turn_id=turn_id, words=word_count(reply.utterance), budget=budget)
                self.store.residue(turn_id, "over_budget_reply",
                                   f"attempt {attempt}: {word_count(reply.utterance)} words > budget {budget}; regenerated",
                                   reply.to_dict())
                turn["regenerations"] = attempt + 1
                msgs[-1]["content"] += f"\nYour previous reply had {word_count(reply.utterance)} words. Stay under {budget}."
                continue
            break
        if reply is None:
            raise ReplyInvalid(f"{turn_id}: no valid reply after {cfg.max_regenerations + 1} attempts")
        usage = gen.usage or {}
        chosen = usage.get("chosen_model") or getattr(self.llm_for(speaker), "model", gen.model)
        mismatch = bool(chosen and gen.model and chosen != gen.model and not usage.get("router")
                        and not chosen.endswith(gen.model) and not gen.model.endswith(chosen))
        turn.update(provider=gen.provider, model=gen.model, llm_params=gen.params, gen_latency_s=gen.latency_s,
                    usage=usage, raw_output=gen.raw, chosen_model=chosen, served_model=gen.model,
                    router=bool(usage.get("router")), served_mismatch=mismatch)
        turn["original_utterance"] = reply.utterance
        if word_count(reply.utterance) > budget:
            reply.utterance = trim_to_words(reply.utterance, budget)
            turn["trimmed"] = True
            self.store.residue(turn_id, "trimmed_text", f"over word budget {budget} after final attempt; cut at sentence boundary",
                               turn["original_utterance"][len(reply.utterance):].strip())
        if reply.unverified_citations:
            self._emit("unverified_citation", turn_id=turn_id, ids=reply.unverified_citations)
        turn["reply"] = reply.to_dict()
        turn["_reply_obj"] = reply
        turn["status"] = "written"
        turn["write_s"] = round((time.monotonic() - t0) - (self._paused_total() - p_start), 4)
        turn["_written_t"] = time.monotonic()
        self._emit("generate_end", turn_id=turn_id, model=gen.model, latency_s=round(gen.latency_s, 3),
                   words=word_count(reply.utterance), move=reply.move)
        return turn

    def _voice(self, epoch: int, turn: dict) -> dict:
        """Synthesize and enforce the audible ceiling on the decoded duration."""
        cfg = self.config
        speaker, turn_id = turn["speaker"], turn["turn_id"]
        reply = turn.pop("_reply_obj")
        p_start, t0 = self._paused_total(), time.monotonic()
        self._checkpoint(epoch, "synthesizing" if cfg.lookahead == 0 else None)
        self.stages["voicer"] = f"voicing {turn_id}"
        text = reply.utterance
        rendered = None
        for attempt in range(4):
            self._request_gate(epoch, f"tts:{speaker}")
            rendered = self.tts_for(speaker).synthesize(text, self.store.audio_path(turn_id),
                                                        words_per_second=self._wps(speaker))
            if self.epoch != epoch:
                self._emit("stale_synthesis_discarded", turn_id=turn_id)
                raise Cancelled()
            if rendered.duration_s <= cfg.max_turn_seconds:
                break
            self._emit("over_ceiling", turn_id=turn_id, duration_s=round(rendered.duration_s, 3),
                       ceiling_s=cfg.max_turn_seconds, attempt=attempt)
            shorter = drop_last_sentence(text)
            new_text = shorter if shorter != text else trim_to_words(text, int(word_count(text) * 0.8))
            self.store.residue(turn_id, "ceiling_shortened_text",
                               f"audio {rendered.duration_s:.2f}s > ceiling {cfg.max_turn_seconds}s",
                               text[len(new_text):].strip())
            text = new_text
            turn["trimmed"] = True
            turn["_ceiling_shortened_t"] = time.monotonic()
        else:
            turn["status"] = "refused_over_ceiling"
            self.turns.append(turn)
            raise ValueError(f"{turn_id}: audio still exceeds {cfg.max_turn_seconds}s after shortening; playback refused")
        reply.utterance = text
        turn.update(reply=reply.to_dict(), wav=str(Path(rendered.path).relative_to(self.store.dir)),
                    wav_sha256=rendered.sha256, duration_s=rendered.duration_s, sample_rate=rendered.sample_rate,
                    channels=rendered.channels, tts_engine=rendered.engine, tts_version=rendered.engine_version,
                    tts_params=rendered.params, synth_time_s=rendered.synth_time_s,
                    voice=getattr(rendered, "voice", "") or rendered.params.get("voice", ""),
                    audio_analysis=getattr(rendered, "analysis", None), status="voiced",
                    voice_s=round((time.monotonic() - t0) - (self._paused_total() - p_start), 4))
        self._emit("synth_end", turn_id=turn_id, duration_s=round(rendered.duration_s, 3), engine=rendered.engine,
                   sha256=rendered.sha256[:16])
        return turn

    def _play(self, epoch: int, turn: dict) -> dict:
        cfg = self.config
        turn_id = turn["turn_id"]
        self._checkpoint(epoch, "playing")
        self.stages["player"] = f"playing {turn_id}"
        # a reply written before an earlier turn was shortened for the ceiling answered words never heard
        by_id = {t["turn_id"]: t for t in self.written}
        for pid in turn.get("prior_turn_ids", []):
            o = by_id.get(pid)
            if o and o.get("_ceiling_shortened_t") and turn.get("_written_t") \
                    and o["_ceiling_shortened_t"] > turn["_written_t"]:
                turn["context_shortened_after_reply"] = True
        turn["status"] = "playing"
        self.turns.append(turn)
        self._emit("play_begin", turn_id=turn_id, duration_s=round(turn["duration_s"], 3))
        try:
            facts = self.player.play(str(self.store.dir / turn["wav"]), turn["duration_s"], cfg.max_turn_seconds,
                                     still_valid=lambda: self.epoch == epoch)
        except PlaybackInterrupted as e:
            turn["status"] = "stopped_during_playback"
            turn["played_s"] = float(e.args[0]) if e.args else None
            self.store.residue(turn_id, "interrupted_turn", f"operator stop at {turn['played_s']}s of {turn['duration_s']:.2f}s",
                               turn["reply"]["utterance"], reopen="only as a new session; never auto-resumed")
            self.store.write_json(f"turns/{turn_id}.json", self._clean(turn))
            raise Cancelled()
        if self.epoch != epoch:
            raise Cancelled()
        turn.update(status="played", played_s=facts["played_s"], paused_s=facts["paused_s"])
        self.store.write_json(f"turns/{turn_id}.json", self._clean(turn))
        self._emit("play_end", turn_id=turn_id, played_s=round(facts["played_s"], 3), paused_s=round(facts["paused_s"], 3))
        return turn

    @staticmethod
    def _clean(turn: dict) -> dict:
        return {k: v for k, v in turn.items() if not k.startswith("_")}

    # ------------------------------------------------------------ pipeline schedule (text ahead of voices)
    def _run_pipeline(self, epoch: int):
        cfg = self.config
        order = [cfg.first_speaker, "B" if cfg.first_speaker == "A" else "A"]
        cond = threading.Condition()
        texts: list[dict] = []      # written turns waiting for a voice, in order
        voiced: list[dict] = []     # voiced turns, in order
        box = {"writer_done": False, "voicer_done": False, "error": None, "play_started": False, "end_at": None}
        self.written, self.preroll = [], {"state": "preparing"}

        def writer():
            try:
                for i in range(cfg.turns):
                    while True:  # bounded lookahead, except during the pre-roll (the pre-roll IS the buffer)
                        self._checkpoint(epoch)
                        # after the pre-roll the writer may stay as far ahead as the buffer the pre-roll built
                        ahead = max(cfg.lookahead, box.get("preroll_ready", 0))
                        if not box["play_started"] or i < len(self.turns) + ahead:
                            break
                        self.stages["writer"] = f"waiting (lookahead {cfg.lookahead})"
                        time.sleep(0.02)
                    t = self._write(epoch, i, order[i % 2], list(self.written))
                    self._checkpoint(epoch)  # a result that came back during Pause is held here
                    with cond:
                        self.written.append(t)
                        texts.append(t)
                        cond.notify_all()
                    if t["reply"]["move"] == "end":
                        self._emit("scene_end_requested", turn_id=t["turn_id"], speaker=t["speaker"])
                        box["end_at"] = i
                        break
            except Cancelled:
                pass
            except Exception as e:  # noqa: BLE001 - handed to the player, reported there
                box["error"] = e
            finally:
                self.stages["writer"] = "done"
                with cond:
                    box["writer_done"] = True
                    cond.notify_all()

        def voicer():
            try:
                while True:
                    with cond:
                        while not texts and not box["writer_done"] and box["error"] is None and self.epoch == epoch:
                            cond.wait(0.05)
                        if self.epoch != epoch or box["error"] is not None or not texts:
                            return
                        t = texts.pop(0)
                    self._voice(epoch, t)
                    self._checkpoint(epoch)
                    with cond:
                        voiced.append(t)
                        cond.notify_all()
                    self.stages["voicer"] = "idle"
            except Cancelled:
                pass
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            finally:
                self.stages["voicer"] = "done"
                with cond:
                    box["voicer_done"] = True
                    cond.notify_all()

        threads = [threading.Thread(target=writer, daemon=True, name="debate-writer"),
                   threading.Thread(target=voicer, daemon=True, name="debate-voicer")]
        for th in threads:
            th.start()
        try:
            self._preroll(epoch, cond, voiced, box)
            i = 0
            while True:
                total = (box["end_at"] + 1) if box["end_at"] is not None else cfg.turns
                if i >= total:
                    break
                with self._lock:
                    hold, self.pending_hold = self.pending_hold, 0.0
                designed = (cfg.gap_seconds if i > 0 else 0.0) + hold
                if designed > 0:
                    self._emit("silence", designed_s=designed, operator_hold_s=hold, before_turn=i + 1)
                    self._wait_gap(epoch, designed)
                # underrun: the voice for turn i is not ready when the designed silence has ended
                t0, p0 = time.monotonic(), self._paused_total()
                self.phase = "waiting"
                self.stages["player"] = f"waiting for turn {i + 1}"
                while True:
                    self._checkpoint(epoch)
                    if box["error"] is not None:
                        raise box["error"]
                    with cond:
                        if len(voiced) > i:
                            break
                        if box["voicer_done"]:
                            if box["error"] is not None:
                                raise box["error"]
                            total = (box["end_at"] + 1) if box["end_at"] is not None else len(voiced)
                            if i >= total:
                                break
                            raise Cancelled()
                        cond.wait(0.02)
                if len(voiced) <= i:
                    break
                wait = round((time.monotonic() - t0) - (self._paused_total() - p0), 4)
                turn = voiced[i]
                turn["designed_silence_s"] = designed
                if i == 0:
                    turn["undesigned_wait_s"] = round(self.preroll.get("preroll_s", 0.0) + wait, 4)
                    turn["preroll"] = True
                else:
                    turn["undesigned_wait_s"] = wait
                    if wait > 0.05:
                        self._emit("underrun", turn_id=turn["turn_id"], wait_s=wait)
                self._play(epoch, turn)
                i += 1
            with self._lock:
                if self.epoch == epoch:
                    self._act("complete", turns=len(self.turns))
        except Cancelled:
            self._emit("worker_cancelled", phase=self.phase, worker_epoch=epoch)
        except (LLMError, TTSError, ReplyInvalid, ValueError) as e:
            self._fail(epoch, e)
        except Exception as e:  # crash is reported as a crash, never as an aesthetic gesture
            self._fail(epoch, e, crash=True)
            raise
        finally:
            self.stages["player"] = "done" if self.epoch == epoch else "stopped"
            for th in threads:
                th.join(timeout=2)
            heard = {t["turn_id"] for t in self.turns}
            for t in list(self.written):
                if t["turn_id"] not in heard and t["status"] != "generated_not_heard":
                    t["status"] = "generated_not_heard"
                    self.store.residue(t["turn_id"], "generated_not_heard",
                                       "written ahead but never played (Stop/New/Quit/error/end)",
                                       (t.get("reply") or {}).get("utterance", ""),
                                       reopen="only as a new session; never auto-played")

    def _preroll(self, epoch: int, cond: threading.Condition, voiced: list[dict], box: dict):
        """Wait until enough voices are buffered that the rest of the debate will not run dry."""
        cfg = self.config
        t0, p0 = time.monotonic(), self._paused_total()
        self.phase = "preroll"
        self.stages["player"] = "pre-roll"
        self._emit("preroll_begin", preroll_min=cfg.preroll_min, max_preroll_s=cfg.max_preroll_s)
        capped = False
        while True:
            self._checkpoint(epoch)
            if box["error"] is not None:
                raise box["error"]
            elapsed = (time.monotonic() - t0) - (self._paused_total() - p0)
            with cond:
                ready = list(voiced)
                all_ready = box["voicer_done"]
            total = (box["end_at"] + 1) if box["end_at"] is not None else cfg.turns
            n = len(ready)
            scale = getattr(self.player, "time_scale", 1.0)  # audible seconds -> wall seconds (1.0 in real use)
            buffered = (sum(t["duration_s"] for t in ready) + cfg.gap_seconds * max(0, n - 1)) * scale
            gens = [t["write_s"] for t in ready if t.get("write_s") is not None]
            syns = [t["voice_s"] for t in ready if t.get("voice_s") is not None]
            # writer and voicer overlap, so the slower of the two sets the production rate
            P = max(sum(gens) / len(gens) if gens else 0.0, sum(syns) / len(syns) if syns else 0.0)
            H = ((sum(t["duration_s"] for t in ready) / n + cfg.gap_seconds) * scale) if n else 0.0
            left = max(0, total - n)
            need = max(0.0, (P - H) * left) if n else None
            missing = max(cfg.preroll_min - n, 0)
            if need is not None and need > buffered and H > 0:
                missing = max(missing, min(left, int((need - buffered) / H) + 1))
            self.preroll = {"state": "preparing", "ready": n, "needed_min": min(cfg.preroll_min, total),
                            "buffered_s": round(buffered, 1), "need_s": None if need is None else round(need, 1),
                            "eta_s": round(missing * P, 1) if P else None, "elapsed_s": round(elapsed, 1),
                            "production_s_per_turn": round(P, 2), "heard_s_per_turn": round(H, 2),
                            "max_preroll_s": cfg.max_preroll_s}
            if all_ready or n >= total:
                break
            if n >= min(cfg.preroll_min, total) and need is not None and buffered >= need:
                break
            if elapsed >= cfg.max_preroll_s and n >= 1:
                capped = True
                break
            with cond:
                cond.wait(0.05)
        self.preroll.update(state="done", capped=capped,
                            preroll_s=round((time.monotonic() - t0) - (self._paused_total() - p0), 4))
        self._emit("preroll_end", **{k: v for k, v in self.preroll.items() if k != "state"})
        if capped:
            self._emit("preroll_capped", note="started at max_preroll_s; underruns are likely")
        box["preroll_ready"] = self.preroll.get("ready", 0)
        box["play_started"] = True
