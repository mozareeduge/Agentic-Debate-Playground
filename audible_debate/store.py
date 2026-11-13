"""Local, append-only session persistence.

sessions/<session_id>/
  config.json        exact configuration snapshot (personas, prompts, knowledge, adapters)
  events.jsonl       every state change, operator action, generation, synthesis and playback fact
  prompts/<turn>.json  exact messages sent to the model for that turn
  turns/<turn>.json  turn record
  audio/<turn>.wav   rendered audio (placeholder audio is labelled as such in the turn record)
  transcript.md, audit.csv, manifest.json   written on Save (rewritten on each Save; events are never rewritten)

Nothing is deleted. `New` opens a fresh session directory.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import secrets
import threading
import time
from pathlib import Path

from .audio import sha256_file


def new_session_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


class SessionStore:
    def __init__(self, root: str | Path, session_id: str | None = None):
        self.session_id = session_id or new_session_id()
        self.dir = Path(root) / self.session_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self._lock = threading.Lock()
        self._seq = 0
        self._events = open(self.dir / "events.jsonl", "a", encoding="utf-8")

    def event(self, type_: str, epoch: int, state: str, **data) -> dict:
        with self._lock:
            self._seq += 1
            rec = {
                "seq": self._seq,
                "t_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                "t_mono": round(time.monotonic(), 4),
                "epoch": epoch,
                "state": state,
                "type": type_,
                **data,
            }
            self._events.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._events.flush()
            return rec

    def write_json(self, rel: str, obj) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def audio_path(self, turn_id: str) -> Path:
        return self.dir / "audio" / f"{turn_id}.wav"

    def snapshot(self, config: dict, turns: list[dict], state: str, mock_or_placeholder: dict) -> dict:
        lines = [f"# Transcript — {self.session_id}", "", f"Question: {config['question']}", "",
                 f"State at save: `{state}`", ""]
        if mock_or_placeholder["any"]:
            lines += ["> **Label:** " + mock_or_placeholder["label"], ""]
        for t in turns:
            r = t.get("reply") or {}
            head = f"## {t['turn_id']} — {t['speaker']} ({t['persona']}) · {t['status']}"
            meta = (f"model `{t.get('model')}` · move `{r.get('move')}` · shift `{r.get('position_shift')}` · "
                    f"uncertainty `{r.get('uncertainty')}` · audio {t.get('duration_s', 0):.1f}s ({t.get('tts_engine')})")
            lines += [head, "", meta, ""]
            if r.get("utterance"):
                lines += [r["utterance"], ""]
            if r.get("verified_source_ids") or r.get("unverified_citations"):
                lines += [f"Sources cited (offered to this agent): {r.get('verified_source_ids')}; "
                          f"unverified citations: {r.get('unverified_citations')}", ""]
        (self.dir / "transcript.md").write_text("\n".join(lines), encoding="utf-8")

        cols = ["turn_id", "speaker", "status", "model", "tts_engine", "duration_s", "gen_latency_s",
                "synth_time_s", "regenerations", "trimmed", "move", "position_shift", "verified_sources",
                "unverified_citations"]
        with open(self.dir / "audit.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for t in turns:
                r = t.get("reply") or {}
                w.writerow([t["turn_id"], t["speaker"], t["status"], t.get("model"), t.get("tts_engine"),
                            round(t.get("duration_s", 0), 3), round(t.get("gen_latency_s", 0), 3),
                            round(t.get("synth_time_s", 0), 3), t.get("regenerations", 0), t.get("trimmed", False),
                            r.get("move"), r.get("position_shift"), ";".join(r.get("verified_source_ids", [])),
                            ";".join(r.get("unverified_citations", []))])

        files = {}
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and p.name != "manifest.json":
                files[str(p.relative_to(self.dir))] = sha256_file(p)
        manifest = {"session_id": self.session_id, "state": state, "turns": len(turns), "files": files,
                    "labels": mock_or_placeholder}
        self.write_json("manifest.json", manifest)
        return manifest

    def close(self):
        with self._lock:
            if not self._events.closed:
                self._events.close()
