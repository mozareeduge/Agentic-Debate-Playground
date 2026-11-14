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


# raised 2026-11-14: free reasoning models cut replies off mid-JSON at the old 900/1400
EFFORT_MAX_TOKENS = {"none": 1600, "low": 2400, "medium": 3600, "high": 6000}
RETRYABLE = (429, 500, 502, 503, 504)


class OpenAICompatAdapter:
    """One performer's brain on one OpenAI-compatible source.

    effort: none | low | medium | high -> `reasoning_effort` (omitted for none) and an
    effort-sized max_tokens so reasoning models do not run out before the JSON reply.
    Failures (429/5xx/timeout/empty content) are retried on the SAME model (2 s, 6 s,
    Retry-After up to 20 s); then LLMError. A rejected optional parameter
    (reasoning_effort, response_format) is dropped once and recorded in `dropped_params`.
    Another model or provider is never substituted.
    """

    provider = "openai-compatible"
    supports_gate = True   # the controller passes gate(): called before every retry, blocks while paused

    def __init__(self, endpoint: str | None, model: str, api_key: str | None = None, structured: bool = True,
                 effort: str = "none", source_id: str = "", router: bool = False, retries: int = 2,
                 backoff_s: tuple = (2.0, 6.0)):
        endpoint = endpoint or os.environ.get("DEBATE_LLM_ENDPOINT")
        if not endpoint:
            raise LLMError("no endpoint given; pass --endpoint or set DEBATE_LLM_ENDPOINT (use `discover` first)")
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1"):
            self.endpoint += "/v1"
        self.model = model
        self.api_key = api_key or os.environ.get("DEBATE_LLM_API_KEY", "")
        self.structured = structured
        if effort not in EFFORT_MAX_TOKENS:
            raise LLMError(f"effort must be one of {list(EFFORT_MAX_TOKENS)}")
        self.effort = effort
        self.source_id = source_id or self.endpoint
        self.router = router
        self.retries, self.backoff_s = retries, backoff_s
        self.dropped_params: list[str] = []
        self._sleep = time.sleep

    @classmethod
    def from_source(cls, source, model: str, effort: str = "none", **kw):
        return cls(source.base_url, model, api_key=source.api_key(), effort=effort, source_id=source.id,
                   router=model in getattr(source, "router_models", []), **kw)

    def describe(self) -> dict:
        return {"provider": self.provider, "source": self.source_id, "endpoint": self.endpoint,
                "model": self.model, "effort": self.effort, "router": self.router,
                "structured": self.structured}

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
            body = e.read()[:400]
            err = LLMError(f"HTTP {e.code} from {self.endpoint}{path}: {body!r}")
            err.status, err.body = e.code, body.decode(errors="replace")
            ra = e.headers.get("Retry-After") if e.headers else None
            err.retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
            raise err from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            err = LLMError(f"endpoint unreachable or timed out ({self.endpoint}{path}): {e}")
            # URLError = could not connect (dead port, wrong address): not retried.
            # A bare timeout after connecting = a slow model: retried on the same model.
            timed_out = isinstance(e, TimeoutError) and not isinstance(e, urllib.error.URLError)
            err.status, err.body, err.retry_after = ("timeout" if timed_out else "unreachable"), "", None
            raise err from e

    def list_models(self, timeout_s: float = 5.0) -> list[str]:
        data = self._request("/models", None, timeout_s)
        return [m.get("id", "?") for m in data.get("data", [])]

    def _payload(self, messages, params) -> dict:
        payload = {"model": self.model, "messages": messages, **params}
        payload["max_tokens"] = max(int(params.get("max_tokens", 0) or 0), EFFORT_MAX_TOKENS[self.effort])
        if self.effort != "none" and "reasoning_effort" not in self.dropped_params:
            payload["reasoning_effort"] = self.effort
        if self.structured and "response_format" not in self.dropped_params:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "debate_reply", "strict": True, "schema": REPLY_JSON_SCHEMA},
            }
        return payload

    min_timeout_s: float = 0.0   # a slow source (account bridges, agy on the VM) raises the session timeout

    def generate(self, messages: list[dict], params: dict, timeout_s: float, gate=None) -> Generation:
        timeout_s = max(timeout_s, self.min_timeout_s)
        t0 = time.monotonic()
        attempts, last = 0, None
        while True:
            if attempts and gate:
                gate()
            payload = self._payload(messages, params)
            try:
                data = self._request("/chat/completions", payload, timeout_s)
                raw = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                if not raw or not str(raw).strip():
                    last = LLMError(f"empty content from {self.model} (finish_reason="
                                    f"{(data.get('choices') or [{}])[0].get('finish_reason')})")
                    last.status, last.retry_after = "empty", None
                    raise last
                served = data.get("model") or self.model
                p = dict(params, effort=self.effort, max_tokens=payload["max_tokens"])
                return Generation(raw=raw, provider=f"{self.provider}:{self.source_id}", model=served, params=p,
                                  latency_s=time.monotonic() - t0,
                                  usage={**(data.get("usage") or {}), "chosen_model": self.model,
                                         "served_model": served, "router": self.router,
                                         "attempts": attempts + 1, "dropped_params": list(self.dropped_params)})
            except LLMError as e:
                status = getattr(e, "status", None)
                body = (getattr(e, "body", "") or "").lower()
                if status == 400:
                    for opt in ("reasoning_effort", "response_format"):
                        if opt in payload and opt not in self.dropped_params and (opt in body or "unsupported" in body
                                                                                    or "not supported" in body):
                            self.dropped_params.append(opt)
                            break
                    else:
                        raise
                    continue
                retryable = status in RETRYABLE or status in ("timeout", "empty")
                # a model that timed out already cost a full timeout: retry it once at most
                limit = min(self.retries, 1) if status == "timeout" else self.retries
                if not retryable or attempts >= limit:
                    raise LLMError(f"{e} (after {attempts + 1} attempt(s) on {self.model}; no other model is used)") from e
                wait = self.backoff_s[min(attempts, len(self.backoff_s) - 1)]
                ra = getattr(e, "retry_after", None)
                if ra:
                    wait = min(20.0, max(wait, ra))
                attempts += 1
                self._sleep(wait)


class BackupAdapter:
    """Owner opt-in (page checkbox, on by default): when the chosen model fails a turn after its own
    retries, the same turn is asked of the backups in order. Never silent: the turn records
    usage.backup_used + backup_reason and the page tags it BACKUP. The chosen model is tried first
    again on every new turn."""

    supports_gate = True

    def __init__(self, primary, backups: list):
        self.primary, self.backups = primary, list(backups)
        self.provider, self.model = getattr(primary, "provider", "?"), getattr(primary, "model", "?")

    def describe(self) -> dict:
        d = self.primary.describe() if hasattr(self.primary, "describe") else {"model": self.model}
        return dict(d, backups=[getattr(b, "model", "?") for b in self.backups])

    def generate(self, messages, params, timeout_s, gate=None):
        errors = []
        for i, a in enumerate([self.primary] + self.backups):
            if i and gate:
                gate()
            try:
                if getattr(a, "supports_gate", False):
                    g = a.generate(messages, params, timeout_s, gate=gate)
                else:
                    g = a.generate(messages, params, timeout_s)
            except LLMError as e:
                errors.append(f"{getattr(a, 'model', '?')}: {str(e)[:140]}")
                continue
            if i:
                g.usage = dict(g.usage or {}, backup_used=True, chosen_model=self.model,
                               backup_model=getattr(a, "model", g.model), backup_reason=errors[-1])
            return g
        raise LLMError("every model failed this turn: " + " | ".join(errors))


def quick_test(adapter: "OpenAICompatAdapter", timeout_s: float = 30.0) -> dict:
    """One tiny request (no retries) so the owner can see which free model answers right now."""
    t0 = time.monotonic()
    try:
        payload = {"model": adapter.model, "max_tokens": 900,
                   "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
        data = adapter._request("/chat/completions", payload, timeout_s)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        ok = bool(content.strip())
        return {"ok": ok, "seconds": round(time.monotonic() - t0, 1), "served_model": data.get("model"),
                "note": "" if ok else "answered with empty content"}
    except LLMError as e:
        return {"ok": False, "seconds": round(time.monotonic() - t0, 1), "note": str(e)[:160]}


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
