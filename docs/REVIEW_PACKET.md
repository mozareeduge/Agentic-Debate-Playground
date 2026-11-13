# Review packet: Audible Debate of Agents, v0 prototype

Date: 2026-11-13 · Branch: `ccr-18cb2ef2-x3dzio` · Handoff: *Proposed handoff v0.1, 2026-11-13*

This packet combines the six return items from handoff §13. It is honest about the build session: **it ran in a Linux cloud container, not on the owner's Windows host.** Statements about Studio, Audial, LM Studio or host hardware are therefore *not verified* here.

---

## 1. Reuse map (Gate 0 — what was actually inspected)

| Component | Status in build session | Evidence |
|---|---|---|
| `Agentic-Debate-Playground` repo | **Present** (empty: only README) | `git log`: single "Initial commit" |
| `mozare-wiki` repo | **Present**, read-only for this work | governance repo; no Studio/Audial code; untouched |
| Mozare Studio, Audial/SuperCollider, Visor/Hydra | **Absent** | not in session; no repo access granted |
| Core (M-64 / Contract 1.9), PocketBase, MAWS | **Absent**: reported only | handoff §5 "prior reported" |
| Hermes / Jimmy delegation framework | **Absent** | — |
| LM Studio endpoint | **Absent** | `discover http://127.0.0.1:1234` → connection refused |
| Windows 11 host, RTX 4060 8 GB | **Not inspected** | cloud container |
| Kokoro / Piper | **Not installed** | `which piper` empty |
| ffmpeg | Present (container only) | `/usr/bin/ffmpeg` |
| Notebook photographs (2 spreads) | **Not received** | the paste carried placeholders only |

**Decision:** there is no verified Studio extension point, so the handoff's fallback applies: *"propose a standalone ephemeral proof with clean boundary instead of restructuring Core."* The boundary is three duck-typed interfaces: `llm.generate(messages, params, timeout)`, `tts.synthesize(text, path, **voice)`, and `player.play/pause/resume/stop`. An Audial/OSC player, or a Core-backed store, plugs in behind those without touching the controller.

**Transfer loss to flag:** the pasted handoff lost every table and code block. That includes the §1 eight-requirement table, the §4 three-round score, the §5 flow diagram, the §6 FSM and interface sketches, the §7 JSON examples, the §8 prompts, the §10 T01–T14 table and the §11 matrix. Everything below that would have come from those blocks was **reconstructed from surrounding prose** and is marked as such.

## 2. Implemented vs proposed

| Requirement (owner source = O, handoff proposal = P) | Implementation |
|---|---|
| O: two agents | `personas.EXAMINER`, `personas.REFRAMER` |
| O: one question | `SessionConfig.question` (default test question: *"Can a machine change its mind?"*) |
| O: distinct principles | separate persona cards and system prompts (**P**: wording is mine, §8 prompts were lost) |
| O: distinct voices / TTS 1, TTS 2 | per-persona speaking rate wired; per-agent voice model selection **not yet**: see open items |
| O: Agent 1 → text → Agent 2 → text alternation | strictly sequential; B's prompt contains A's completed text (test T01) |
| O: knowledge / tools | `none` + `curated` per agent; `search` raises `NotImplementedError` (Gate 3) |
| O: turn ceiling | 30–120 s, default 120 s, measured from **decoded WAV samples**; word budget → regenerate → sentence trim → resynthesize → refuse playback |
| O: manual lifecycle | Start / Pause / Resume / Stop / Save / New / Quit via FSM; no autoplay; `--start-now` is logged as `source: cli --start-now` |
| P: event history | append-only `events.jsonl` with monotonic `seq`, `epoch` and state; replayable (`verify`) |
| P: JSON reply cue | `utterance, move, addresses, position_shift, uncertainty, source_ids, analogy`; only `utterance` reaches TTS (T12) |
| P: citation honesty | cited IDs split into `verified_source_ids` (offered to *that* agent) and `unverified_citations` |
| P: epoch cancellation | stop/new/quit/fail bump the epoch; late generation, synthesis or playback is discarded and logged |
| P: speechless fallback | `SpeechlessTTS` silent WAVs, labelled in every turn, transcript and manifest |
| P: mock adapter | `MockAdapter`, labelled "MOCK" in transcript and manifest |
| P: three debug measures | `metrics.run_metrics`: opponent reference, lexicon/Jaccard, concession/shift/uncertainty |

**Deviations from the handoff, and why**

1. **No pre-generation during playback.** The handoff allows it under conditions. v0 generates strictly after playback ends, so Pause never hides background work. Cost: a silent wait equal to model latency plus synthesis time between turns. Measure that on the host before changing it.
2. **Pause during generation** cannot abort an in-flight HTTP request. The result is *parked* at the next checkpoint and is not synthesized or played until Resume, or it is discarded on Stop (T05, T04).
3. **No Studio UI.** The "one view" is the terminal CLI. Studio controls wait on Gate 0 on the real host.
4. **Kokoro adapter not written.** I didn't want to ship untested code; Piper is a thin CLI wrapper and is **also unverified**.
5. **Retry from `error`** is done via `new` (fresh session, same config), not an in-place retry.

## 3. Pilot run (mock, labelled)

The only runs in this packet are **MOCK text + SILENT PLACEHOLDER audio**. No live-generated debate exists yet. Excerpt from a 6-turn run (75 s ceiling):

> **t01A Examiner:** Under what conditions would we say a machine has a mind to change? Show me the observation first. …
> **t02B Reframer:** You ask for an observation. Perhaps the observation is this exchange: you said "whether that is a change of mind is exactly what", and I am no longer answering the question I began with. …
> **t03A Examiner:** You said "in that sense something has moved". Which observation would separate that from a change of weights or a change of prompt? …

`verify`: 35 events, actions `configure → start → complete → save → quit`, replayed state `closed`, 6 turns, max duration 20.03 s ≤ 75 s, **no problems**.
`metrics` (descriptors only): A references opponent 0.5, B 0.67, vocabulary Jaccard A/B 0.208, concessions 1, shifts 2.
These numbers describe a scripted mock. They say nothing about a real model.

## 4. Acceptance report

```
$ python -m unittest discover -s tests -v
Ran 18 tests ... OK            (5 consecutive runs: OK ×5, ResourceWarnings promoted to errors)
```

The handoff's T01–T14 table was lost in transfer. The IDs below are **mine**, derived from the prose of §§5–6, 10 and 12:

| ID | Check | Result |
|---|---|---|
| T01 | 6 alternating turns; each sees the opponent's completed text; replay verifies; mock labelled | pass |
| T02 | Pause freezes playback position and turn advancement; Resume continues from the same position | pass |
| T03 | Stop during playback halts before the end; no further turns appear afterwards | pass |
| T04 | Stop during generation discards the late result; no audio written | pass |
| T05 | Pause during generation parks the result; nothing synthesized until Resume | pass |
| T06 | 480-word reply at a 30 s ceiling: 2 regenerations, trim, measured WAV ≤ 30 s; player refuses > ceiling | pass |
| T07 | Non-JSON output: error state, no fabricated argument | pass |
| T08 | Unreachable model: `error`, configured model ID recorded, no substitution | pass |
| T09 | Source asymmetry: B never sees A's excerpt; B's citation of it flagged unverified; `search` refused | pass |
| T10 | New opens a fresh session directory; the old one is preserved | pass |
| T11 | Ceiling outside 30–120 s rejected | pass |
| T12 | TTS receives only the cleaned utterance (no JSON, no source IDs) | pass |
| + | FSM legality, no autoplay after stop/error, schema validation, sentence trimming | pass |

Also exercised by hand: the interactive CLI piped `start, pause, <status>, resume, stop, resume (refused: not allowed in stopped), save, quit` and produced the expected states.

**Not tested (requires the host):** a real model, real voices, real audio device latency, Windows pause (`ProcessPlayer` uses POSIX SIGSTOP/SIGCONT, so **Pause will not freeze audio on Windows** as written), GPU/RAM contention with SuperCollider and Visor, and long-run stability.

## 5. Change log

New files only, all in `Agentic-Debate-Playground`: `audible_debate/*.py` (10 modules), `tests/test_debate.py`, `examples/sources_a.json`, `docs/REVIEW_PACKET.md`, `README.md` (rewritten from a one-line title), `.gitignore`.
`mozare-wiki`: **no changes.** No Studio, Core, provider, device or projector state was touched. Rollback is to delete the branch.

## 6. Owner review: open decisions only

1. **Photographs and lost tables.** Please commit the two notebook spreads and the handoff with its tables intact (e.g. as PDF/DOCX). The §1 requirement table and §10 T01–T14 were reconstructed, not read.
2. **Voices.** Choose the TTS engine and one voice per performer (Kokoro vs Piper), checked on the host.
3. **Language** of the performance.
4. **Adversarial or exploratory?** The prompts currently allow both; the mock leans exploratory.
5. **Persona wording.** `personas.py` is my draft in place of the lost §8 prompts. Please revise or approve.
6. **Duration.** Keep the 120 s ceiling, or use the 45–75 s pilot suggestion? Also the total length and the inter-turn gap (1.5 s).
7. **Exposure.** Should the audience see the transcript, citations or clock, and should errors be audible?

### Next exact operation (on the Windows host, by Jimmy or the owner)

```powershell
git clone <playground> ; cd Agentic-Debate-Playground ; git checkout ccr-18cb2ef2-x3dzio
python -m unittest discover -s tests -v
python -m audible_debate discover http://localhost:1234
python -m audible_debate run --endpoint http://localhost:1234 --model <id> --speechless --max-turn-seconds 75
```

That is the first **live-generated** text run. Then run Gate 0 against Studio/Audial, so that an `AudialPlayer` (OSC transport) can replace `ProcessPlayer` behind the same interface.
