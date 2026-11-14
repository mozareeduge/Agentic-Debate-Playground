"""Structured reply schema and validation.

The model returns a performance cue. Only `utterance` is ever sent to TTS.
Validation runs even when the model/provider claims structured output support.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MOVES = ("assert", "question", "object", "concede", "qualify", "reframe", "refuse_premise", "end")
SHIFTS = ("none", "partial", "reversed")
UNCERTAINTY = ("low", "medium", "high")

REPLY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["utterance", "move", "addresses", "position_shift", "uncertainty", "source_ids", "analogy"],
    "properties": {
        "utterance": {"type": "string", "description": "Plain spoken text only."},
        "move": {"type": "string", "enum": list(MOVES)},
        "addresses": {"type": "string", "description": "Short quote or paraphrase of the opponent claim you respond to; empty on the opening turn."},
        "position_shift": {"type": "string", "enum": list(SHIFTS)},
        "uncertainty": {"type": "string", "enum": list(UNCERTAINTY)},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "analogy": {"type": "boolean"},
    },
}


class ReplyInvalid(Exception):
    pass


@dataclass
class Reply:
    utterance: str
    move: str
    addresses: str
    position_shift: str
    uncertainty: str
    source_ids: list[str]
    analogy: bool
    verified_source_ids: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
_SPEECH_JUNK = re.compile(r"[{}\[\]<>*#`_|]")


def _extract_json(raw: str) -> dict:
    text = _FENCE.sub("", raw.strip())
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ReplyInvalid("no JSON object in model output")
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise ReplyInvalid(f"malformed JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ReplyInvalid("top-level JSON is not an object")
    return obj


def clean_for_speech(text: str) -> str:
    """Remove scaffolding characters so TTS never reads markup or JSON."""
    text = _SPEECH_JUNK.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


_MOVE_ALIASES = {"opening": "assert", "open": "assert", "claim": "assert", "answer": "assert",
                 "challenge": "object", "objection": "object", "rebut": "object", "counter": "object",
                 "agree": "concede", "refine": "qualify", "closing": "end", "close": "end", "ask": "question"}


_STR_FIELD = r'"{k}"\s*:\s*("(?:[^"\\]|\\.)*")'


def _salvage(raw: str) -> dict:
    """A free model often runs out of tokens after a complete utterance (2026-11-14 live: 4 of 4 failures).
    Keep every field whose value arrived whole; the utterance itself must be a closed string."""
    m = re.search(_STR_FIELD.format(k="utterance"), raw, re.S)
    if not m:
        raise ReplyInvalid("no JSON object in model output")
    obj = {"utterance": json.loads(m.group(1))}
    for k in ("move", "addresses", "position_shift", "uncertainty"):
        mk = re.search(_STR_FIELD.format(k=k), raw, re.S)
        if mk:
            obj[k] = json.loads(mk.group(1))
    return obj


def _level(v) -> str | None:
    """0.0-1.0 or 1-10 numbers -> low/medium/high."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    x = v / 10 if v > 1 else v
    return "low" if x < 0.34 else "medium" if x < 0.67 else "high"


def parse_reply(raw: str, allowed_source_ids: set[str]) -> Reply:
    """The spoken text is strict (must be a non-empty string). The cue labels around it are repaired
    when a free model sends null, a number or a near-synonym; every repair is listed in `repaired`."""
    repaired: list[str] = []
    try:
        obj = _extract_json(raw)
    except ReplyInvalid:
        obj = _salvage(raw)   # raises ReplyInvalid again when no complete utterance is there
        repaired.append("reply was cut off after the spoken text; labels filled from what arrived")
    if not isinstance(obj.get("utterance"), str):
        raise ReplyInvalid("utterance missing or not a string")
    utterance = clean_for_speech(obj["utterance"].strip())
    if not utterance:
        raise ReplyInvalid("empty utterance")

    def _enum(k, allowed, default, alias=None, numeric=False):
        v = obj.get(k)
        if isinstance(v, str) and v.strip().lower() in allowed:
            return v.strip().lower()
        if isinstance(v, str) and alias and v.strip().lower() in alias:
            out = alias[v.strip().lower()]
        elif numeric and _level(v):
            out = _level(v)
        elif k == "position_shift" and v in (0, False, None):
            out = "none"
        else:
            out = default
        repaired.append(f"{k}: {v!r} -> {out!r}")
        return out

    move = _enum("move", MOVES, "assert", _MOVE_ALIASES)
    shift = _enum("position_shift", SHIFTS, "none")
    unc = _enum("uncertainty", UNCERTAINTY, "medium", numeric=True)
    addr = obj.get("addresses")
    if not isinstance(addr, str):
        out = "; ".join(str(a) for a in addr) if isinstance(addr, list) else ""
        repaired.append(f"addresses: {addr!r} -> {out!r}")
        addr = out
    sids = obj.get("source_ids")
    if not isinstance(sids, list) or not all(isinstance(s, str) for s in sids):
        repaired.append(f"source_ids: {sids!r} -> []")
        sids = []
    analogy = obj.get("analogy")
    if not isinstance(analogy, bool):
        repaired.append(f"analogy: {analogy!r} -> False")
        analogy = False
    verified = [s for s in sids if s in allowed_source_ids]
    unverified = [s for s in sids if s not in allowed_source_ids]
    return Reply(
        utterance=utterance, move=move, addresses=addr.strip(), position_shift=shift,
        uncertainty=unc, source_ids=list(sids), analogy=analogy,
        verified_source_ids=verified, unverified_citations=unverified, repaired=repaired,
    )


def word_count(text: str) -> int:
    return len(text.split())


def trim_to_words(text: str, max_words: int) -> str:
    """Trim at the last sentence boundary within max_words; hard cut only if no boundary exists."""
    words = text.split()
    if len(words) <= max_words:
        return text
    head = " ".join(words[:max_words])
    cut = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    if head.endswith((".", "?", "!")):
        return head
    if cut > 0:
        return head[: cut + 1]
    return head.rstrip(",;:") + "."


def drop_last_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return " ".join(parts[:-1]) if len(parts) > 1 else text
