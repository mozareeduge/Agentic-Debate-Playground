# Handoff to local Jimmy: Audible Debate of Agents

From: the cloud build session (Claude Code, Linux container) · Date: 2026-11-13
To: Jimmy, on the owner's Windows host · Repo: `mozareeduge/Agentic-Debate-Playground`
Branch: `ccr-18cb2ef2-x3dzio` (head `f1a2525`) · PR: #1 (**draft; do not merge**)

This file is self-contained. You have not seen the conversation that produced it.

---

## 0. Your first rule (unchanged from the owner's handoff v0.1)

Do not assume anything below is true on the host. **Inspect first, report differences.**

The cloud session could **not** see any of these:
- Mozare Studio, Audial/SuperCollider, Visor/Hydra, Core, PocketBase or Hermes;
- LM Studio;
- the Windows machine;
- any voice engine;
- the two notebook photographs.

Everything about those components is either reported or owner-stated in the wiki. **None of it is verified.**

Do not overwrite product or performance-governance decisions. Do not touch active Studio sessions, production state, public outputs, the projector or physical devices without explicit owner permission. Do not merge, tag, publish, or ingest anything into the Wiki or 0xBrain.

## 1. What exists (built and tested in the cloud)

A standalone Python package `audible_debate/`. It is stdlib only, needs no installs, and requires Python 3.10 or newer. It has a clean boundary, so Studio can adopt it later without restructuring Core.

| Part | State |
|---|---|
| Lifecycle FSM: start / pause / resume / stop / save / new / quit; `cue` and `hold` operator actions | done, tested |
| Strict A → text → B alternation, bounded history, persona prompts (**draft**) | done, tested with mock |
| JSON reply cue and validation; only the cleaned `utterance` reaches TTS | done, tested |
| Ceiling of 30–120 s/turn, measured from **decoded WAV**: regenerate → trim → resynthesize → refuse | done, tested with silent WAVs |
| Epoch cancellation: late LLM, TTS or playback results after Stop are discarded | done, tested |
| Per-agent knowledge (`none`, `curated`); verified vs unverified citations; `search` refused | done, tested |
| Append-only session store, residue, event replay (`verify`), metrics | done, tested |
| `OpenAICompatAdapter` for LM Studio (no hardcoded endpoint, no fallback) | **written, never run against a real server** |
| `PiperTTS` (CLI wrapper) | **written, never run** |
| `ProcessPlayer` (ffplay/aplay/afplay) | **pause is POSIX-only and is a no-op on Windows** (see §4.3) |
| Kokoro adapter, Audial/OSC player, Studio UI | **not written** |

Tests: `python -m unittest discover -s tests -v` gives 23 passing in the cloud.

Read next, in this order:
1. `docs/REVIEW_PACKET.md`: reuse map, deviations, tests, owner questions.
2. `docs/AESTHETICS_AND_METHOD.md`: how the Mozare Wiki shaped the design. Each choice has a source and an authority level.

## 2. Known gaps you are best placed to close

1. **Lost source content.** The owner's handoff reached the cloud with every table, diagram and code block stripped. Reconstructed parts:
   - the §1 eight-requirement table,
   - the §4 three-round score,
   - the §5 flow diagram,
   - the §6 FSM and screen sketches,
   - the §7 JSON examples,
   - the §8 prompts,
   - the §10 T01–T14 table,
   - the §11 matrix.

   **If you have the original handoff and photographs, diff them against `docs/REVIEW_PACKET.md` §2/§4 and `audible_debate/personas.py`, and list every deviation.** Do not silently "fix" the owner's prompts into the code. Report first.
2. **Personas and prompts** in `personas.py` are cloud drafts. They are not owner text.
3. The **speaking rates** (A 2.3 w/s, B 2.6 w/s) are guesses. They drive the word budget. Calibrate them from real TTS output (§4.2).

## 3. Gate 0 on the host (read-only, change nothing)

Report each item as **verified present / reported but unverified / missing**:

```powershell
# repo and tests
git clone https://github.com/mozareeduge/Agentic-Debate-Playground ; cd Agentic-Debate-Playground
git checkout ccr-18cb2ef2-x3dzio
python --version
python -m unittest discover -s tests -v          # expect 23 OK; report any Windows-only failure verbatim

# LM Studio: read-only probe (GET /v1/models); do not change its defaults or download models
python -m audible_debate discover http://localhost:1234 http://127.0.0.1:1234
```

Also inspect, without changing anything:
- **Audial:** its transport (OSC address and port, message schema), the audio device it owns, and its sample rate and channel count. **Do not assume 44.1 or 48 kHz.**
- Whether **Studio has a stable extension point** for a small control view, and whether Core has a session or media layer that should own `sessions/`.
- **GPU and VRAM load** with the LLM loaded, and with SuperCollider and Visor running if they normally run. Do not interrupt active sessions.
- Which voice engines are installed (Kokoro, Piper), and their licences and voices.

Write the results to `docs/HOST_REUSE_MAP.md` on the branch.

## 4. Gates 1–2 on the host

### 4.1 First live text run (placeholder audio, labelled)

```powershell
python -m audible_debate run --endpoint http://localhost:1234 --model <exact id from discover> --speechless --max-turn-seconds 75
# type: start ... (pause/resume/stop/save/quit as needed)
python -m audible_debate verify  sessions\<id>
python -m audible_debate metrics sessions\<id>
```

What to watch for:
- **Structured output.** If the model rejects `response_format`, re-run with `--no-structured`. Validation still applies. Record which mode worked.
- **Latency.** `metrics.beat.max_undesigned_wait_s` is the silent wait between turns. v0 does **not** pre-generate during playback, by design, so nothing hidden runs while paused. Report the measured waits. Do not enable pre-generation without owner agreement.
- **The three descriptors:** does each turn reference the opponent's claim, are the lexicons distinct, and do concessions or shifts occur? Also report the smoothing flags (`closure`, `meta_commentary`, `taxonomy`) and the convergence values from the first round to the last.

**Same model for both performers first.** Compare different models only afterwards, so persona effects aren't confused with model effects.

### 4.2 Voices

1. Before involving the LLM, prove playback of two static short WAV clips through the **existing Audial path**. Test stop and pause. Do not touch system-wide audio settings.
2. Audition Kokoro and Piper on the host. Piper is already wrapped (`--piper-model PATH`). For Kokoro, write a `KokoroTTS` class with the same `synthesize(text, out_path, **kw) -> Rendered` contract as `audio.SpeechlessTTS`. Measure duration from the written WAV with `audio.measure_wav`, never from text.
3. Record for each engine: synthesis time, memory, stability, and voice distinctiveness. Keep the speechless fallback.
4. Calibrate `Persona.speaking_rate_wps` from measured durations. Report old and new values; changing them is a code change on the branch.

### 4.3 Playback on Windows: must fix before any audible pilot

`ProcessPlayer` pauses with SIGSTOP/SIGCONT, which **does not exist on Windows**. Pause would freeze the clock and turn advancement but **not the sound**. Choose one, in this order of preference:

1. **`AudialPlayer`:** subclass `audio.ClockPlayer` and implement `_device_start/_device_pause/_device_resume/_device_stop` as Audial OSC (or Audial's existing) messages. This reuses the owner's own transport; ANJA used OSC cue bridges, according to the wiki.
2. A streaming player (for example `sounddevice` or `simpleaudio`) whose pause genuinely stops output. This adds a dependency, so record it.

The player must:
- keep `play()` blocking;
- honour `still_valid()` on every tick, so a late callback can never restart sound;
- refuse durations over the ceiling (inherited behaviour);
- return actual position on stop.

Stop must silence output quickly. Measure that latency and report it.

### 4.4 Audible acceptance on the host (report each with raw evidence)

1. Run a six-turn live-generated audition with real voices: continuous, alternating and human-started.
2. Report the measured duration per turn. Every turn must be at or under the ceiling, as confirmed by `verify`.
3. Pause and resume repeatedly during speech and during generation. The audio must actually freeze.
4. Stop during speech and during TTS generation. There must be no stray queued speech afterwards.
5. Crash or restart recovery. Kill the process mid-turn, then restart. The session must come back stopped or recoverable, **never autoplaying**.
6. Run one mock control-only session alongside, labelled as mock.

Stop condition (owner's): never claim the pilot "works" because two requests returned text or two WAVs were synthesized. A valid pilot needs all of the following:
- human-controlled, continuous two-way audition;
- timing enforcement;
- persistence;
- preserved evidence.

## 5. Constraints carried from the Mozare Wiki (read-only context)

- Use the wiki **read-only** for context on practice, methods and aesthetics. Do not create canonical pages for this work. If capture is wanted, it goes through the wiki's own intake (`/wiki-intake`) as a **proposal**, and only on the owner's request.
- The owner's 2026-11-11 sound principles shape the silence and timing design here. They are designed decay, off-beat against a crafted on-beat, artistic ruining as a designed process, a precise unexpected entry, and restrained voice fragmentation. They were stated for the owner's **own sound work**, so applying them to this piece is a **candidate reading awaiting owner confirmation**. Do not extend their use further without asking. In particular, **do not build a decaying ending or voice fragmentation** on your own initiative.
- Report crashes as crashes, never as aesthetic gestures. This is the design-side meaning of "ruining is artistic only when designed".
- Do not describe the two agents as "collective enunciation". The wiki warns explicitly against equating multi-agent software with it.

## 6. Do not

- Merge PR #1, tag, publish, or auto-ingest session recordings anywhere.
- Hardcode the LM Studio URL into the product, or silently substitute another model or provider.
- Use player speed-up to fit long text under the ceiling.
- Clone any identifiable person's voice.
- Change Hermes, Core, PocketBase, providers, plugins, Studio sessions, output or projector settings, or credentials.
- Force-push. Commit only to `ccr-18cb2ef2-x3dzio`, or to a new branch off it.
- Commit `sessions/`, models, voices, caches or machine-specific paths.

## 7. Return packet (append to the branch, then report to the owner)

| File | Contents |
|---|---|
| `docs/HOST_REUSE_MAP.md` | Gate 0 findings: verified / unverified / missing, entry points, host measurements |
| `docs/SOURCE_DEVIATIONS.md` | original handoff and photos vs the cloud reconstruction (§2.1) |
| `docs/HOST_ACCEPTANCE.md` | §4.4 table with raw evidence, latencies, resource use, failures |
| `docs/PILOT_EXCERPTS.md` | short transcript excerpts and session IDs (recordings stay local) |
| PR #1 comment | exact paths changed, tests run and results, open items. **No self-approval.** |

Then list **only** the decisions the owner must make next. The current list, to be confirmed or pruned:
- voice engine and voices;
- language;
- adversarial or exploratory;
- persona wording;
- turn ceiling and gap;
- audience exposure;
- whether the 11-11 sound principles apply to turn-taking and silence;
- whether the ending should be designed;
- which cues are for performance and which for rehearsal;
- whether a closure flag on the last turn means failure or material.

## 8. Next exact operation

```powershell
git clone https://github.com/mozareeduge/Agentic-Debate-Playground ; cd Agentic-Debate-Playground ; git checkout ccr-18cb2ef2-x3dzio
python -m unittest discover -s tests -v
python -m audible_debate discover http://localhost:1234
```

Report all three results before running anything else.
