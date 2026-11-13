"""Descriptive measures for recorded runs, plus replay/verification.

The three measures from the handoff's debug requirement:
  (a) reference to the opponent's actual claim
  (b) distinct lexical / syntactic pattern per performer
  (c) change, concession and uncertainty events
plus three readings drawn from the Mozare Wiki (candidate relations, not owner-approved):
  (d) smoothing flags: closure/reconciliation phrases, meta-commentary, list/taxonomy speech
      (wiki: smoothing-machine; token-distribution-techniques "Meta-Commentary Ban", "Anti-Taxonomy")
  (e) convergence: do A and B drift into one vocabulary from the first round to the last?
  (f) the beat: designed silence vs undesigned wait (latency) before each turn
      (wiki: artistic-ruining-as-designed-process — ruining that is not designed is not a gesture)
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


CLOSURE = re.compile(r"\b(in conclusion|to sum up|to summari[sz]e|we (both|all) agree|we agree|ultimately|"
                     r"at the end of the day|common ground|both (of us )?(are|were) right|in the end)\b", re.I)
META = re.compile(r"\b(as an ai|as a language model|my (response|reply|answer) (is|will)|let me explain|"
                  r"i will now|in this (turn|reply|response))\b", re.I)
TAXONOMY = re.compile(r"(^|[.?!]\s+)(first(ly)?|second(ly)?|third(ly)?|finally)\b,?|\b\d\)|\b(three|four|several) "
                      r"(types|kinds|categories|ways)\b", re.I)


def smoothing_flags(text: str) -> list[str]:
    flags = []
    if CLOSURE.search(text):
        flags.append("closure")
    if META.search(text):
        flags.append("meta_commentary")
    if TAXONOMY.search(text):
        flags.append("taxonomy")
    return flags


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
            "smoothing_flags": smoothing_flags(r["utterance"]),
            "operator_cue": (t.get("operator_cue") or {}).get("technique"),
            "designed_silence_s": t.get("designed_silence_s"),
            "undesigned_wait_s": t.get("undesigned_wait_s"),
        }
        per_turn.append(row)
        by_speaker[t["speaker"]].append(r["utterance"])
    va = content_words(" ".join(by_speaker["A"]))
    vb = content_words(" ".join(by_speaker["B"]))
    responders = [r for r in per_turn if r["references_opponent"] is not None]

    def round_jaccard(rows):
        a = content_words(" ".join(t["reply"]["utterance"] for t in rows if t["speaker"] == "A"))
        b = content_words(" ".join(t["reply"]["utterance"] for t in rows if t["speaker"] == "B"))
        return round(len(a & b) / max(1, len(a | b)), 3) if a and b else None

    waits = [r["undesigned_wait_s"] for r in per_turn if r["undesigned_wait_s"] is not None]
    gaps = [(r["designed_silence_s"], r["undesigned_wait_s"]) for r in per_turn[1:]
            if r["designed_silence_s"] is not None and r["undesigned_wait_s"] is not None]
    return {
        "turns": per_turn,
        "a_references_opponent_rate": _rate([r for r in responders if r["speaker"] == "A"]),
        "b_references_opponent_rate": _rate([r for r in responders if r["speaker"] == "B"]),
        "vocabulary_jaccard_A_B": round(len(va & vb) / max(1, len(va | vb)), 3),
        "concessions": sum(r["move"] == "concede" for r in per_turn),
        "position_shifts": sum(r["position_shift"] != "none" for r in per_turn),
        "high_uncertainty": sum(r["uncertainty"] == "high" for r in per_turn),
        "smoothing_flags": {f: sum(f in r["smoothing_flags"] for r in per_turn) for f in ("closure", "meta_commentary", "taxonomy")},
        "closing_turn_flags": per_turn[-1]["smoothing_flags"] if per_turn else [],
        "convergence_first_round_jaccard": round_jaccard(played[:2]),
        "convergence_last_round_jaccard": round_jaccard(played[-2:]) if len(played) > 2 else None,
        "beat": {
            "max_undesigned_wait_s": max(waits) if waits else None,
            "mean_undesigned_wait_s": round(sum(waits) / len(waits), 3) if waits else None,
            "gaps_where_latency_exceeded_designed_silence": sum(u > d for d, u in gaps),
            "note": "designed silence is in audible seconds; undesigned wait is wall time spent generating/synthesizing (pauses excluded)",
        },
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
    residue = d / "residue.jsonl"
    residue_n = sum(1 for l in residue.read_text(encoding="utf-8").splitlines() if l.strip()) if residue.exists() else 0
    return {"session": d.name, "events": len(events), "actions": actions, "replayed_state": final,
            "residue_records": residue_n,
            "turns_recorded": len(turns), "ceiling_s": ceiling,
            "max_duration_s": max((t["duration_s"] for t in turns), default=0), "problems": problems,
            "ok": not problems}
