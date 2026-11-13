"""Descriptive measures for recorded runs, plus replay/verification.

The three measures from the handoff's debug requirement:
  (a) reference to the opponent's actual claim
  (b) distinct lexical / syntactic pattern per performer
  (c) change, concession and uncertainty events
They describe a run. They are not judgments of artistic quality.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import fsm
from .audio import measure_wav, sha256_file
from .personas import PERSONAS

STOP = set("""a an the and or but if of to in on at for with is are was were be been it its this that these those
i you we they he she not no do does did can could would should may might will so as by from than then there
what which who whom how why when where""".split())


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOP and len(w) > 2}


def run_metrics(turns: list[dict]) -> dict:
    played = [t for t in turns if t.get("reply")]
    per_turn, by_speaker = [], {"A": [], "B": []}
    for i, t in enumerate(played):
        r = t["reply"]
        prev_opp = next((p for p in reversed(played[:i]) if p["speaker"] != t["speaker"]), None)
        overlap = len(content_words(r["utterance"]) & content_words(prev_opp["reply"]["utterance"])) if prev_opp else None
        lex = PERSONAS[t["speaker"]].lexicon
        hits = sum(r["utterance"].lower().count(p) for p in lex)
        sentences = [s for s in re.split(r"(?<=[.?!])\s+", r["utterance"]) if s]
        row = {
            "turn_id": t["turn_id"], "speaker": t["speaker"],
            "addresses_field": bool(r.get("addresses")),
            "opponent_content_overlap": overlap,
            "references_opponent": (None if prev_opp is None else bool(r.get("addresses")) and (overlap or 0) >= 2),
            "persona_lexicon_hits": hits,
            "mean_sentence_words": round(sum(len(s.split()) for s in sentences) / max(1, len(sentences)), 1),
            "question_ratio": round(sum(s.endswith("?") for s in sentences) / max(1, len(sentences)), 2),
            "move": r["move"], "position_shift": r["position_shift"], "uncertainty": r["uncertainty"],
            "analogy": r["analogy"],
        }
        per_turn.append(row)
        by_speaker[t["speaker"]].append(r["utterance"])
    va = content_words(" ".join(by_speaker["A"]))
    vb = content_words(" ".join(by_speaker["B"]))
    responders = [r for r in per_turn if r["references_opponent"] is not None]
    return {
        "turns": per_turn,
        "a_references_opponent_rate": _rate([r for r in responders if r["speaker"] == "A"]),
        "b_references_opponent_rate": _rate([r for r in responders if r["speaker"] == "B"]),
        "vocabulary_jaccard_A_B": round(len(va & vb) / max(1, len(va | vb)), 3),
        "concessions": sum(r["move"] == "concede" for r in per_turn),
        "position_shifts": sum(r["position_shift"] != "none" for r in per_turn),
        "high_uncertainty": sum(r["uncertainty"] == "high" for r in per_turn),
        "note": "descriptors only; not a quality score",
    }


def _rate(rows):
    return None if not rows else round(sum(bool(r["references_opponent"]) for r in rows) / len(rows), 2)


def verify_session(session_dir: str | Path) -> dict:
    """Replay the event log through the FSM and check every turn's WAV hash and duration."""
    d = Path(session_dir)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    actions = [e["action"] for e in events if e["type"] == "action"]
    problems = []
    try:
        final = fsm.replay(actions)
    except fsm.IllegalTransition as e:
        final, problems = None, [f"event log replay failed: {e}"]
    logged = [e["to_state"] for e in events if e["type"] == "action"]
    if logged and final and logged[-1] != final:
        problems.append(f"replayed state {final} != logged {logged[-1]}")
    turns = []
    for p in sorted((d / "turns").glob("*.json")) if (d / "turns").exists() else []:
        t = json.loads(p.read_text(encoding="utf-8"))
        turns.append(t)
        wav = d / t["wav"]
        if not wav.exists():
            problems.append(f"{t['turn_id']}: missing audio")
            continue
        if sha256_file(wav) != t["wav_sha256"]:
            problems.append(f"{t['turn_id']}: audio hash mismatch")
        dur, _, _ = measure_wav(wav)
        if abs(dur - t["duration_s"]) > 1e-3:
            problems.append(f"{t['turn_id']}: duration mismatch")
    cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
    ceiling = cfg["config"]["max_turn_seconds"]
    over = [t["turn_id"] for t in turns if t.get("played_s", 0) > ceiling + 1e-6 or t["duration_s"] > ceiling + 1e-6]
    if over:
        problems.append(f"turns over ceiling: {over}")
    return {"session": d.name, "events": len(events), "actions": actions, "replayed_state": final,
            "turns_recorded": len(turns), "ceiling_s": ceiling,
            "max_duration_s": max((t["duration_s"] for t in turns), default=0), "problems": problems,
            "ok": not problems}
