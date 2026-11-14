# Host reuse map (Gate 0) — Windows host, 2026-11-14

Inspected by Jimmy in Claude Code (Opus 5.5). Read-only except where stated. Status words: **verified present** (observed now, with evidence), **reported, unverified**, **missing**.

| Piece | Status | Evidence / entry point |
|---|---|---|
| Repo + branch `ccr-18cb2ef2-x3dzio` | verified present | cloned to `C:\Users\Zarinpal\Documents\Agentic-Debate-Playground` |
| Python | verified present | 3.14.3 (needs ≥ 3.10) |
| Cloud test suite on Windows | verified | 23/23 pass, unchanged, before any edit |
| LM Studio (`:1234`) | missing (not running) | `GET /v1/models` refused (WinError 10061) |
| Ollama (`:11434`) | missing | refused; no `ollama` on PATH |
| Kilo free gateway (`:28124`) | verified present | 11 free models listed; `nemotron-3-ultra-550b-a55b:free` answered the debate JSON prompt in 5.8 s, `kilo-auto/free` in 14.8 s (routed to `dots-3-note-preview:free`); `laguna-s-2.1:free` and `xing4.0-29b-a4b:free` returned no content |
| 9router (`:20128`): OpenCode Zen free + Cline free lanes | present, **needs key** | `/models` answers; chat returns "Missing API key". Key name `HERMES_CUSTOM_ROUTER_API_KEY` (from Hermes config, value not read: credentials stay the owner's decision). Not in the user environment |
| opencode CLI free models | present | `opencode run -m …`; not used (process spawn per call, 4–20 s); the same Zen models are reachable over 9router |
| Line Machine voices (`:4795`) | verified present | started hidden with its own venv (no browser, no sound); `/api/voices` = 51 voices (Persian Piper ×11, Windows English ×7, Gemini ×30, Fish, Edge ×2); `/api/speak` → 22 050 Hz mono WAV; English 0.4–0.9 s, Persian 1.3–4.2 s per line |
| Windows audio playback with real pause | verified (silently) | winmm MCI via ctypes: pause froze the device position (600 → 600 ms), stop 43–46 ms; tested only with silent WAVs |
| GPU / VRAM | verified | Intel UHD Graphics only, no discrete GPU; 23.7 GB RAM → a local LLM would run on CPU (slow). Free remote gateways are the practical source today |
| Audial transport (OSC address/port, schema, device, sample rate) | reported, unverified | quick read-only search of `mozare-intermedia-studio/audial` found no OSC port; not inspected further (Studio is live work; do not touch) |
| Studio extension point for a control view | reported, unverified | not inspected beyond HANDOFF; the debate exposes a small HTTP API (`/api/status`, `/api/action`) that a Studio view could call later |
| Kokoro | missing | not installed |
| Piper CLI | present inside Line Machine only | used through Line Machine `/api/speak`; standalone `piper` not on PATH |

## What the debate now reuses
- **Models:** any OpenAI-compatible free source listed in `config/sources.example.json` (copy to `config/sources.json` to change locally). Discovery is live and read-only.
- **Voices:** the Line Machine over HTTP. The debate never edits Line Machine files and never sends `save` (which writes packets into its folder).
- **Playback:** Windows MCI (stdlib), not Audial. Audial/OSC remains the preferred long-term path (handoff §4.3 option 1) once its transport is verified.

## Incident during inspection (repaired)
While probing Line Machine, four test requests were sent with `save:true`, which wrote four packet files into `anja-line-machine/packets/`. They were identified by name and time (created minutes earlier) and deleted; no other file there was touched. The adapter never sends `save`, and a test asserts it.
