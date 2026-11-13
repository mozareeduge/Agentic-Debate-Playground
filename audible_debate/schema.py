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


def parse_reply(raw: str, allowed_source_ids: set[str]) -> Reply:
    obj = _extract_json(raw)
    missing = [k for k in REPLY_JSON_SCHEMA["required"] if k not in obj]
    if missing:
        raise ReplyInvalid(f"missing fields: {missing}")
    def _str(k):
        if not isinstance(obj[k], str):
            raise ReplyInvalid(f"{k} must be a string")
        return obj[k].strip()
    utterance = clean_for_speech(_str("utterance"))
    if not utterance:
        raise ReplyInvalid("empty utterance")
    move, shift, unc = _str("move"), _str("position_shift"), _str("uncertainty")
    if move not in MOVES:
        raise ReplyInvalid(f"move '{move}' not in {MOVES}")
    if shift not in SHIFTS:
        raise ReplyInvalid(f"position_shift '{shift}' not in {SHIFTS}")
    if unc not in UNCERTAINTY:
        raise ReplyInvalid(f"uncertainty '{unc}' not in {UNCERTAINTY}")
    sids = obj["source_ids"]
    if not isinstance(sids, list) or not all(isinstance(s, str) for s in sids):
        raise ReplyInvalid("source_ids must be a list of strings")
    if not isinstance(obj["analogy"], bool):
        raise ReplyInvalid("analogy must be a boolean")
    verified = [s for s in sids if s in allowed_source_ids]
    unverified = [s for s in sids if s not in allowed_source_ids]
    return Reply(
        utterance=utterance, move=move, addresses=_str("addresses"), position_shift=shift,
        uncertainty=unc, source_ids=list(sids), analogy=obj["analogy"],
        verified_source_ids=verified, unverified_citations=unverified,
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
