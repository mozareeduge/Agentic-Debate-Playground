"""Debate controller: alternating turns under human lifecycle control.

Sequencing is strictly A -> text -> B -> text -> A ... Each speaker receives
the question, the opponent's completed last turn, and a bounded shared history.
v0 does NOT pre-generate during playback: the next turn is generated only after
the previous one has finished playing, so nothing hidden runs while paused.

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

    def validate(self):
        if not self.question.strip():
            raise ValueError("question is empty")
        if not (MIN_CEILING_S <= self.max_turn_seconds <= MAX_CEILING_S):
            raise ValueError(f"max_turn_seconds must be within {MIN_CEILING_S:.0f}–{MAX_CEILING_S:.0f}")
        if self.turns < 1:
            raise ValueError("turns must be >= 1")
        if self.first_speaker not in PERSONAS:
            raise ValueError("first_speaker must be A or B")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["knowledge"] = {k: v.to_dict() for k, v in self.knowledge.items()}
        return d


class Cancelled(Exception):
    pass


class Controller:
    def __init__(self, llm, tts, player: ClockPlayer | None = None, sessions_root: str | Path = "sessions",
                 on_event=None):
        self.llm, self.tts = llm, tts
        self.player = player or ClockPlayer()
        self.sessions_root = Path(sessions_root)
        self.on_event = on_event or (lambda e: None)
        self.state = fsm.IDLE
        self.epoch = 0
        self.config: SessionConfig | None = None
        self.store: SessionStore | None = None
        self.turns: list[dict] = []
        self.phase = ""
        self._lock = threading.RLock()
        self._resume = threading.Event()
        self._resume.set()
        self._worker: threading.Thread | None = None
        self.last_error: str | None = None
        self.pending_cue: dict | None = None
        self.pending_hold: float = 0.0
        self._paused_wall = 0.0
        self._pause_t: float | None = None

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

    def describe_adapters(self) -> dict:
        return {
            "llm": {"provider": getattr(self.llm, "provider", "?"), "model": getattr(self.llm, "model", "?")},
            "tts": {"engine": getattr(self.tts, "name", "?"), "version": getattr(self.tts, "version", "?")},
            "player": {"class": type(self.player).__name__, "time_scale": getattr(self.player, "time_scale", 1.0)},
        }

    def start(self, source: str = "operator"):
        with self._lock:
            self._act("start", source=source)
            epoch = self.epoch
            self.player.reset()
            self._resume.set()
            self._worker = threading.Thread(target=self._run, args=(epoch,), daemon=True, name="debate-worker")
            self._worker.start()

    def pause(self):
        with self._lock:
            self._act("pause", phase=self.phase, position_s=round(self.player.position_s, 3))
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
            self._act("stop", phase=self.phase, position_s=round(self.player.position_s, 3))
            self.player.stop()
            self._resume.set()  # unblock a paused worker so it can observe the new epoch and exit

    def cue(self, technique: str, note: str = ""):
        """Place a constraint on the NEXT turn only (replaces any unconsumed cue; both are logged)."""
        instruction = cue_instruction(technique, note)
        with self._lock:
            replaced = self.pending_cue
            self._act("cue", technique=technique, note=note, instruction=instruction,
                      replaced=replaced["technique"] if replaced else None)
            self.pending_cue = {"technique": technique, "note": note, "instruction": instruction}

    def hold(self, seconds: float):
        """Add designed silence before the next turn (0–30 s; accumulates until consumed)."""
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
            self.turns = []
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
        mock = getattr(self.llm, "provider", "") == "mock"
        placeholder = getattr(self.tts, "name", "") == "speechless-placeholder"
        parts = []
        if mock:
            parts.append("text from the deterministic MOCK adapter, not a live model")
        if placeholder:
            parts.append("audio is SILENT PLACEHOLDER (speechless test mode), not synthesized speech")
        return {"any": bool(parts), "mock_llm": mock, "placeholder_audio": placeholder, "label": "; ".join(parts)}

    # ------------------------------------------------------------ worker
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

    def _wait_gap(self, epoch: int, seconds: float):
        scale = getattr(self.player, "time_scale", 1.0)
        remaining = seconds
        last = time.monotonic()
        self.phase = "gap"
        while remaining > 0:
            self._checkpoint(epoch)
            now = time.monotonic()
            remaining -= (now - last) / scale
            last = now
            time.sleep(min(0.02, max(remaining * scale, 0)))

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
            self.last_error = f"{type(e).__name__}: {e}"
            with self._lock:
                if self.epoch == epoch and self.state in (fsm.RUNNING, fsm.PAUSED):
                    self._act("fail", phase=self.phase, error=self.last_error)
        except Exception as e:  # crash is reported as a crash, never as an aesthetic gesture
            self.last_error = f"CRASH {type(e).__name__}: {e}"
            with self._lock:
                if self.epoch == epoch and self.state in (fsm.RUNNING, fsm.PAUSED):
                    self._act("fail", phase=self.phase, error=self.last_error)
            raise

    def _score_position(self, index: int) -> str:
        rounds = (self.config.turns + 1) // 2
        r = index // 2 + 1
        phase = "opening" if index < 2 else ("closing" if r == rounds else "middle")
        return f"ROUND {r} OF {rounds} ({phase}); turn {index + 1} of {self.config.turns}"

    def _messages(self, index: int, speaker: str, cue: dict | None = None) -> tuple[list[dict], list[str], int]:
        cfg = self.config
        persona = PERSONAS[speaker]
        kp: KnowledgeProfile = cfg.knowledge[speaker]
        budget = max(20, int(cfg.max_turn_seconds * persona.speaking_rate_wps * cfg.word_budget_fraction))
        played = [t for t in self.turns if t["status"] == "played"]
        history = played[-cfg.history_turns:]
        opp = next((t for t in reversed(played) if t["speaker"] != speaker), None)
        earlier = [t for t in history if t is not opp]
        hist_txt = "\n".join(f"[{t['turn_id']} {t['speaker']}] {t['reply']['utterance']}" for t in earlier)
        user = [f"QUESTION: {cfg.question}", f"TURN INDEX: {index}", f"YOU ARE: performer {speaker}",
                f"SCORE POSITION: {self._score_position(index)}", ""]
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

    def _do_turn(self, epoch: int, index: int, speaker: str, designed_silence_s: float = 0.0) -> dict:
        cfg = self.config
        persona = PERSONAS[speaker]
        kp: KnowledgeProfile = cfg.knowledge[speaker]
        turn_id = f"t{index + 1:02d}{speaker}"
        t_start, p_start = time.monotonic(), self._paused_total()
        with self._lock:
            cue, self.pending_cue = self.pending_cue, None
        msgs, prior_ids, budget = self._messages(index, speaker, cue)
        prompt_path = self.store.write_json(f"prompts/{turn_id}.json", msgs)
        turn = {"turn_id": turn_id, "session_id": self.store.session_id, "index": index, "speaker": speaker,
                "persona": persona.name, "prior_turn_ids": prior_ids, "sources_offered": sorted(kp.ids),
                "knowledge_mode": kp.mode, "prompt_file": str(prompt_path.relative_to(self.store.dir)),
                "word_budget": budget, "regenerations": 0, "trimmed": False, "status": "generating",
                "invalid_outputs": [], "operator_cue": cue, "designed_silence_s": designed_silence_s,
                "score_position": self._score_position(index)}

        # ---- generate (with validation and budget-driven regeneration)
        self._checkpoint(epoch, "generating")
        self._emit("generate_begin", turn_id=turn_id, speaker=speaker, word_budget=budget)
        reply, gen = None, None
        for attempt in range(cfg.max_regenerations + 1):
            gen = self.llm.generate(msgs, cfg.llm_params, cfg.llm_timeout_s)
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
        turn.update(provider=gen.provider, model=gen.model, llm_params=gen.params, gen_latency_s=gen.latency_s,
                    usage=gen.usage, raw_output=gen.raw)
        turn["original_utterance"] = reply.utterance
        if word_count(reply.utterance) > budget:
            reply.utterance = trim_to_words(reply.utterance, budget)
            turn["trimmed"] = True
            self.store.residue(turn_id, "trimmed_text", f"over word budget {budget} after final attempt; cut at sentence boundary",
                               turn["original_utterance"][len(reply.utterance):].strip())
        if reply.unverified_citations:
            self._emit("unverified_citation", turn_id=turn_id, ids=reply.unverified_citations)
        self._emit("generate_end", turn_id=turn_id, model=gen.model, latency_s=round(gen.latency_s, 3),
                   words=word_count(reply.utterance), move=reply.move)

        # ---- synthesize and enforce the audible ceiling on decoded duration
        self._checkpoint(epoch, "synthesizing")
        text = reply.utterance
        rendered = None
        for attempt in range(4):
            rendered = self.tts.synthesize(text, self.store.audio_path(turn_id), words_per_second=persona.speaking_rate_wps)
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
        else:
            turn["status"] = "refused_over_ceiling"
            self.turns.append(turn)
            raise ValueError(f"{turn_id}: audio still exceeds {cfg.max_turn_seconds}s after shortening; playback refused")
        reply.utterance = text
        turn.update(reply=reply.to_dict(), wav=str(Path(rendered.path).relative_to(self.store.dir)),
                    wav_sha256=rendered.sha256, duration_s=rendered.duration_s, sample_rate=rendered.sample_rate,
                    channels=rendered.channels, tts_engine=rendered.engine, tts_version=rendered.engine_version,
                    tts_params=rendered.params, synth_time_s=rendered.synth_time_s)
        self._emit("synth_end", turn_id=turn_id, duration_s=round(rendered.duration_s, 3), engine=rendered.engine,
                   sha256=rendered.sha256[:16])

        # ---- play
        self._checkpoint(epoch, "playing")
        turn["status"] = "playing"
        turn["undesigned_wait_s"] = round((time.monotonic() - t_start) - (self._paused_total() - p_start), 4)
        self.turns.append(turn)
        self._emit("play_begin", turn_id=turn_id, duration_s=round(rendered.duration_s, 3))
        try:
            facts = self.player.play(rendered.path, rendered.duration_s, cfg.max_turn_seconds,
                                     still_valid=lambda: self.epoch == epoch)
        except PlaybackInterrupted as e:
            turn["status"] = "stopped_during_playback"
            turn["played_s"] = float(e.args[0]) if e.args else None
            self.store.residue(turn_id, "interrupted_turn", f"operator stop at {turn['played_s']}s of {rendered.duration_s:.2f}s",
                               reply.utterance, reopen="only as a new session; never auto-resumed")
            self.store.write_json(f"turns/{turn_id}.json", turn)
            raise Cancelled()
        if self.epoch != epoch:
            raise Cancelled()
        turn.update(status="played", played_s=facts["played_s"], paused_s=facts["paused_s"])
        self.store.write_json(f"turns/{turn_id}.json", turn)
        self._emit("play_end", turn_id=turn_id, played_s=round(facts["played_s"], 3), paused_s=round(facts["paused_s"], 3))
        return turn
