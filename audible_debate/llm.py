"""Language model adapters.

MockAdapter       deterministic, offline; for tests and control-only rehearsals.
OpenAICompatAdapter  any OpenAI-compatible chat endpoint (e.g. LM Studio's local server).
                  The endpoint is never hardcoded: pass it explicitly or via DEBATE_LLM_ENDPOINT.
                  An unavailable model is an error; there is no silent fallback to another provider.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .schema import REPLY_JSON_SCHEMA


class LLMError(Exception):
    pass


@dataclass
class Generation:
    raw: str
    provider: str
    model: str
    params: dict
    latency_s: float
    usage: dict = field(default_factory=dict)


class MockAdapter:
    """Deterministic performer stand-in. Clearly labelled 'mock' in every record.

    It reads the persona key and the opponent's last utterance out of the
    prompt so tests exercise the real context plumbing, and it emits a
    concession and a position shift at fixed turns so metrics have something
    to detect.
    """

    provider = "mock"
    model = "mock-deterministic-v1"

    OPEN = {
        "A": "Under what conditions would we say a machine has a mind to change? Show me the observation first. "
             "A system that updates its output after new input has changed its state. Whether that is a change of mind is exactly what is in question.",
        "B": "Suppose we start from the other end. Perhaps a mind is not a thing that changes but a pattern of changes that we learn to recognise.",
    }
    A_LINES = [
        "You said {q}. Which observation would separate that from a change of weights or a change of prompt? "
        "Without one, what follows is a description, not an argument.",
        "I concede one point. If the change persists and alters later answers, that is more than a reset. "
        "But show me that it was reasons, not pressure, that produced it.",
        "Then let us be precise. A change of mind needs a before, an after, and a reason that connects them. "
        "Under what conditions could either of us report all three honestly?",
    ]
    B_LINES = [
        "You ask for an observation. Perhaps the observation is this exchange: you said {q}, and I am no longer answering the question I began with. In that sense something has moved.",
        "What if persistence is the wrong test? A river changes its course without remembering the old bed. "
        "That is an analogy, not evidence, but it suggests that what has changed may matter more than who recalls it.",
        "Suppose we both changed position just now. What has changed is not the machine alone but the question we are holding between us.",
    ]

    def __init__(self, latency_s: float = 0.0):
        self.latency_s = latency_s

    @staticmethod
    def _quote(text: str) -> str:
        sent = re.split(r"(?<=[.?!])\s+", text.strip())
        first = sent[-1] if sent else text
        words = first.rstrip(".?!").split()
        return " ".join(words[:10]).lower()

    def generate(self, messages: list[dict], params: dict, timeout_s: float) -> Generation:
        t0 = time.monotonic()
        if self.latency_s:
            time.sleep(self.latency_s)
        system = messages[0]["content"]
        user = messages[-1]["content"]
        key = "A" if "The Examiner" in system else "B"
        m = re.search(r"TURN INDEX: (\d+)", user)
        turn = int(m.group(1)) if m else 0
        m = re.search(r"OPPONENT LAST TURN \[[^\]]*\]:\n(.+?)\n\n", user, re.S)
        opp = m.group(1).strip() if m else ""
        has_source = 'id="' in user
        if not opp:
            utterance, move, addresses = self.OPEN[key], "assert", ""
            shift = "none"
        else:
            lines = self.A_LINES if key == "A" else self.B_LINES
            idx = (turn // 2 - 1 + (0 if key == "A" else 1)) % len(lines)
            q = self._quote(opp)
            utterance = lines[idx].format(q=f'"{q}"')
            addresses = q
            move = {0: "question" if key == "A" else "reframe", 1: "concede" if key == "A" else "reframe", 2: "qualify"}[idx]
            shift = "partial" if move == "concede" or (key == "B" and idx == 2) else "none"
        source_ids = []
        if has_source and opp:
            sid = re.search(r'<source id="([^"]+)"', user).group(1)
            source_ids = [sid]
        reply = {
            "utterance": utterance, "move": move, "addresses": addresses, "position_shift": shift,
            "uncertainty": "medium", "source_ids": source_ids, "analogy": "analogy" in utterance,
        }
        return Generation(raw=json.dumps(reply), provider=self.provider, model=self.model, params=dict(params),
                          latency_s=time.monotonic() - t0,
                          usage={"prompt_sha256": hashlib.sha256(user.encode()).hexdigest()[:16]})


class OpenAICompatAdapter:
    provider = "openai-compatible"

    def __init__(self, endpoint: str | None, model: str, api_key: str | None = None, structured: bool = True):
        endpoint = endpoint or os.environ.get("DEBATE_LLM_ENDPOINT")
        if not endpoint:
            raise LLMError("no endpoint given; pass --endpoint or set DEBATE_LLM_ENDPOINT (use `discover` first)")
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1"):
            self.endpoint += "/v1"
        self.model = model
        self.api_key = api_key or os.environ.get("DEBATE_LLM_API_KEY", "")
        self.structured = structured

    def _request(self, path: str, payload: dict | None, timeout_s: float) -> dict:
        req = urllib.request.Request(self.endpoint + path, method="POST" if payload is not None else "GET")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout_s) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code} from {self.endpoint}{path}: {e.read()[:300]!r}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"endpoint unreachable or timed out ({self.endpoint}{path}): {e}") from e

    def list_models(self, timeout_s: float = 5.0) -> list[str]:
        data = self._request("/models", None, timeout_s)
        return [m.get("id", "?") for m in data.get("data", [])]

    def generate(self, messages: list[dict], params: dict, timeout_s: float) -> Generation:
        payload = {"model": self.model, "messages": messages, **params}
        if self.structured:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "debate_reply", "strict": True, "schema": REPLY_JSON_SCHEMA},
            }
        t0 = time.monotonic()
        data = self._request("/chat/completions", payload, timeout_s)
        try:
            raw = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from e
        served = data.get("model") or self.model
        return Generation(raw=raw, provider=self.provider + ":" + self.endpoint, model=served, params=dict(params),
                          latency_s=time.monotonic() - t0, usage=data.get("usage") or {})


def discover(candidates: list[str], timeout_s: float = 2.0) -> list[dict]:
    """Probe candidate endpoints read-only (GET /v1/models). Changes nothing."""
    out = []
    for url in candidates:
        try:
            models = OpenAICompatAdapter(url, model="").list_models(timeout_s)
            out.append({"endpoint": url, "reachable": True, "models": models})
        except LLMError as e:
            out.append({"endpoint": url, "reachable": False, "error": str(e)})
    return out
