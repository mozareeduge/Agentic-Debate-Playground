"""Model sources: every OpenAI-compatible endpoint the owner can use for free, listed live.

A source is one entry in config/sources.json (local, gitignored) or, when that file is
absent, config/sources.example.json (committed, no secrets):

  {"id": "kilo-free", "label": "Kilo · free", "base_url": "http://127.0.0.1:28124/v1",
   "key_env": null, "key_file": null, "free": {"suffix": [":free", "-free"]},
   "router_models": ["kilo/kilo-auto/free"], "local": false}

free:   "all"                      every model the source lists (local servers: LM Studio, Ollama)
        {"suffix": [...]}          ids ending with one of these
        {"allow": [...]}           exactly these ids
        Pricing metadata on /models (pricing.prompt == pricing.completion == "0") always counts as free.
keys:   key_env (process environment) or key_file (a one-line key file, or "dotenv:<path>#<NAME>").
        Keys are never written to session records or returned by the API.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
EFFORTS = ("none", "low", "medium", "high")


@dataclass
class Source:
    id: str
    label: str
    base_url: str
    key_env: str | None = None
    key_file: str | None = None
    free: object = "all"
    router_models: list = field(default_factory=list)
    local: bool = False
    note: str = ""

    def api_key(self) -> str | None:
        if self.key_env and os.environ.get(self.key_env):
            return os.environ[self.key_env]
        if self.key_file:
            return _read_key_file(self.key_file, self.key_env)
        return None

    def needs_key(self) -> bool:
        return bool(self.key_env or self.key_file)

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "base_url": self.base_url, "local": self.local,
                "key_env": self.key_env, "needs_key": self.needs_key(), "has_key": bool(self.api_key()),
                "note": self.note}


def _read_key_file(spec: str, name: str | None) -> str | None:
    path, _, var = spec.partition("#")
    if path.startswith("dotenv:"):
        path = path[len("dotenv:"):]
    var = var or name
    try:
        text = Path(os.path.expandvars(os.path.expanduser(path))).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if var:
        for l in lines:
            k, sep, v = l.partition("=")
            if sep and k.strip().removeprefix("export ").strip() == var:
                return v.strip().strip('"').strip("'") or None
        return None
    return lines[0] if len(lines) == 1 else None


def config_path() -> Path:
    local = CONFIG_DIR / "sources.json"
    return local if local.exists() else CONFIG_DIR / "sources.example.json"


def load_sources(path: str | Path | None = None) -> list[Source]:
    p = Path(path) if path else config_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    return [Source(**{k: v for k, v in s.items() if k in Source.__dataclass_fields__}) for s in data["sources"]]


def is_free(source: Source, model: dict) -> tuple[bool, str]:
    mid = model.get("id", "")
    pricing = model.get("pricing") or {}
    if pricing and str(pricing.get("prompt")) in ("0", "0.0") and str(pricing.get("completion")) in ("0", "0.0"):
        return True, "price 0 in the source's model list"
    rule = source.free
    if rule == "all":
        return True, "local / free source"
    if isinstance(rule, dict):
        if mid in rule.get("allow", []):
            return True, "on the allow list"
        for suf in rule.get("suffix", []):
            if mid.endswith(suf):
                return True, f"id ends with {suf!r}"
    return False, "not marked free"


def _get_models(source: Source, timeout_s: float) -> list[dict]:
    req = urllib.request.Request(source.base_url.rstrip("/") + "/models")
    key = source.api_key()
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode()).get("data", [])


def discover_source(source: Source, timeout_s: float = 4.0) -> dict:
    out = source.public()
    if source.needs_key() and not source.api_key():
        out.update(status="needs_key", models=[],
                   action=f"This source needs its key. Put the key in the environment variable {source.key_env} "
                          f"or point key_file in config/sources.json at the file that holds it, then press Refresh.")
        # still list what it would offer, without a key some gateways answer /models
    try:
        models = _get_models(source, timeout_s)
    except urllib.error.HTTPError as e:
        out.setdefault("status", "offline")
        out.setdefault("reason", f"HTTP {e.code}")
        out.setdefault("models", [])
        return out
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        out["reason"] = str(getattr(e, "reason", e))[:120]
        out["models"] = []
        if out.get("status") != "needs_key":
            out.update(status="offline", action="Start that program (or ignore this source).")
        return out
    listed = []
    for m in models:
        ok, why = is_free(source, m)
        if ok:
            listed.append({"id": m.get("id"), "why_free": why, "router": m.get("id") in source.router_models})
    out["models"] = listed
    out.setdefault("status", "ok" if listed else "no_free_models")
    return out


def discover_all(sources: list[Source] | None = None, timeout_s: float = 4.0) -> list[dict]:
    sources = sources if sources is not None else load_sources()
    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as ex:
        return list(ex.map(lambda s: discover_source(s, timeout_s), sources))


def get_source(source_id: str, sources: list[Source] | None = None) -> Source:
    for s in sources if sources is not None else load_sources():
        if s.id == source_id:
            return s
    raise KeyError(f"unknown source {source_id!r}")
