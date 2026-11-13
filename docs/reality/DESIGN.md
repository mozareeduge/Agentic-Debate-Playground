# Design: Audible Debate v1 (text-ahead pipeline, per-performer brains, Line Machine voices)

Written after the needs were frozen (`REALITY.json`). Each section names the need it serves.

## Surveyed / picked / custom (owner build principle)

| Piece | Surveyed | Picked | Why |
|---|---|---|---|
| Free LLMs | LM Studio (installed? not running), Ollama (absent), Kilo free gateway `:28124` (live, no key), 9router `:20128` (live; OpenCode Zen free + Cline free lanes; key in the existing Hermes env var `HERMES_CUSTOM_ROUTER_API_KEY`), opencode CLI (works but ~4–20 s spawn per call) | every OpenAI-compatible source listed in one config file; live `/v1/models` discovery with a free filter | the machine already runs these gateways; one HTTP contract covers all of them, stdlib only |
| Voices | Line Machine `/api/speak` (Persian Piper voices, Windows Zira/David/Mark, Edge/Gemini/Fish/ElevenLabs), Piper CLI (wrapper exists), Kokoro (not installed) | Line Machine over HTTP; Piper and silent placeholder kept | owner named Line Machine; it already owns voices, textures, cloud keys. The debate calls it, never edits it |
| Windows playback with a real pause | SIGSTOP (POSIX only), ffplay (not installed), `sounddevice`/`simpleaudio` (new deps), winmm MCI via ctypes | **MCI (winmm) via ctypes** | stdlib only, real pause/resume/stop of WAV output, can be tested silently with a silent WAV |
| Control page | Studio extension point (unknown), Gradio/Streamlit (deps), stdlib `http.server` + one HTML page | stdlib server + one page, polling JSON | no installs; runs beside the CLI; Studio can adopt the same API later |

Custom code is only glue: the pipeline scheduler, the source registry, the Line Machine adapter, the MCI player and the page.

## N1 — no dead air: text-ahead pipeline with a voice queue and pre-roll

Three workers per run, all under the same epoch and pause gate:

```
writer ──text──▶ voicer ──WAV──▶ player
(A,B,A… as text,   (TTS + ceiling       (waits for PRE-ROLL voices,
 bounded lookahead) enforcement)          then plays in order + designed gap)
```

- **Writer** writes turn N+1 as soon as turn N's *text* exists (strict A→B alternation is kept; each speaker answers the opponent's last *written* turn). It stays at most `lookahead` turns (default 3) ahead of what has been heard, so it never runs away from the operator.
- **Voicer** synthesizes in order and enforces the 30–120 s ceiling on the decoded WAV (regenerate → trim → resynthesize → refuse, unchanged). If shortening cuts text that a later turn already answered, the turn is flagged `context_shortened_after_reply` (residue keeps the cut text).
- **Player** does not start until `preroll` voices (default 2) are ready, or the writer has finished. Then it plays turn after turn with the designed gap. A late voice is an **underrun**: logged as undesigned wait, never relabelled as designed silence.
- Metrics: `preroll_s` (announced start latency), per-turn `undesigned_wait_s` = underrun after the gap, `max_underrun_after_first_s`.
- Operator `cue` lands on the next turn still to be *written*; the page shows which turn and how many turns until it is heard. `hold` still lands on the next heard gap.
- Owner decision recorded: v0's "no pre-generation" was a default awaiting owner agreement; the owner asked for text-ahead on 2026-11-14 (OM-907). `lookahead=0, preroll=1` reproduces v0 behaviour.

## N2 — per-performer source / model / effort

- `config/sources.json` (local, gitignored; `sources.example.json` committed): list of sources `{id, label, base_url, key_env, env_file?, free_filter, local}`. Keys are read from an env var or a named local env file; never stored in the repo or in session records.
- Discovery: `GET /v1/models` per source, filtered to free models (ids containing `free`; local sources list all). Offline sources are shown as offline, not hidden.
- Per performer: `{source, model, effort}`; effort ∈ `none|low|medium|high` → `reasoning_effort` (omitted for `none`) plus an effort-sized `max_tokens` so reasoning models do not return empty content. If a source rejects `reasoning_effort` or `response_format`, the call is retried once without it and the turn records the dropped parameter (logged, never silent). A different model is never substituted.
- Session `config.json` stores each performer's choice; each turn stores the model the source actually served (e.g. Kilo's `kilo-auto/free` router).

## N3 — Line Machine voices

- `LineMachineTTS(base_url, voice, texture='dry')` POSTs `/api/speak`, writes the returned WAV, measures it with `measure_wav`. Voices listed live from `/api/voices`.
- One voice per performer (default: a Persian or English voice by question language; owner picks). The Line Machine runs hidden on `:4795`; the page offers a "start voices" button that starts it hidden (no browser, no sound) if it is not up.
- Fallback when Line Machine is down: an error on the page, not a silent switch. Placeholder audio stays available and is labelled.

## N4 — control holds on Windows

- `MciPlayer(ClockPlayer)`: winmm `open/play/pause/resume/stop/close` on one dedicated command thread; `play()` stays blocking, honours `still_valid()` every tick, refuses over-ceiling durations, returns position on stop.
- Pause: player pauses the device; writer and voicer park at their next checkpoint; in-flight results are held until resume (or discarded on stop).
- Stop/New/Quit: epoch bump; queued voices and texts are discarded into residue (`generated_not_heard`); the player only ever plays items whose epoch matches.
- Restart never autoplays: the app starts in `ready`; nothing plays until Start.

## N5 — ready to use

- `START-DEBATE.bat` (double-click): starts the local server and opens `http://127.0.0.1:4797/`.
- One page: question, per performer source/model/effort/voice, turns, ceiling, gap, pre-roll, lookahead; Start/Pause/Resume/Stop/Save/New; a pipeline strip (written · voiced · heard); live transcript; labels for mock/placeholder.
- `docs/RUNBOOK.html`: ADHD run-book, card 1 = double-click → first heard turn.

## Revision 2 — fixes after R1 (R1_REPORT.md, all needs `partly`)

Observed on this laptop before revising (silent probes, 2026-11-14): Kilo free models answered a JSON debate prompt in 5.8–16.6 s (`nemotron-3-ultra-550b-a55b:free` 5.8 s, `kilo-auto/free` 14.8 s routed to `dots-3-note-preview:free`); two Kilo models returned no content. Line Machine `/api/speak` returned 22 050 Hz mono WAV: English Windows voices 0.8–0.9 s to synthesize, Persian Piper voices 3–4.2 s; a 17-word line ≈ 5.6–6.9 s of audio. The 9router key is not in the user environment.

### N1 — timing made real
1. **Adaptive pre-roll.** The player starts when *all* hold: ≥ `preroll_min` voices ready (default 2), and buffered audio ≥ the deficit the rest of the debate will need: `need_s = max(0, (P − H) × turns_left)`, where P = mean production time per turn (write + voice, measured live) and H = mean heard time per turn (duration + gap). Or when every turn is ready. Or when `max_preroll_s` (default 90 s) passes; then the event `preroll_capped` says underruns are likely. No guessing: P and H come from measured turns.
2. **Visible countdown.** The page shows "Preparing: k of n voices ready · starts in about N s" (N from the same formula), so the start never looks hung.
3. **Silent rehearsal mode** = real model + real Line Machine synthesis + silent real-time clock player. It measures pre-roll and underruns with no sound, so N1's metric is measured without breaking the silent-AV rule. The audible run is a near-final check that needs the owner's permission for that occasion; it is recorded as pending, not passed.
4. **Rate limits / timeouts.** A 429, 5xx, timeout or empty content is retried up to 2 times on the *same* model with backoff (2 s, 6 s; honours `Retry-After` up to 20 s). Then the turn fails, the run goes to `error`, the page says which performer's model failed and offers New. Never a substituted model.

### N2 — selection made checkable
1. **Free filter** per source in config: `free: "all"` (local sources: LM Studio, Ollama), `free: {"suffix": [":free", "-free"]}` (Kilo, OpenCode Zen, Cline), or an explicit `allow` list; if `/models` carries pricing (`pricing.prompt == "0"` and `pricing.completion == "0"`), that wins. Each listed model shows *why* it counts as free.
2. **Keys.** `key_env` (process env) or `key_file` (a local file the owner points to; a single-line key file or a dotenv file + name). Missing key → that source shows "needs key" with the exact name; it is never hidden, never guessed. Keys never enter session records.
3. **Chosen vs served.** Each turn records `chosen_model` and `served_model`. Sources/models marked `router: true` (e.g. `kilo-auto/free`) are expected to differ and are labelled "router"; for others a difference is flagged `served_mismatch` on the page and in metrics.
4. **Discovery** runs all sources in parallel with a 4 s timeout each; offline sources show "offline (reason)".

### N3 — real speech made provable
1. Each turn records `tts_engine`, `voice`, `sample_rate`, `channels`, `duration_s`, `rms`, `peak`, `speech_like` (rms above −45 dBFS for ≥ 30 % of 50 ms frames). Placeholder turns are `speech_like=false` and labelled.
2. A Line Machine reply that is not RIFF/WAVE is refused (`TTSError`), never decoded by guess.
3. The adapter never sends `save` (that would write packets into the Line Machine folder).
4. `verify` reports turns over ceiling (must be 0) and the count of `speech_like` turns.

### N4 — control made measurable
1. **Paused means:** no new model or voice request *starts*; an in-flight request may finish but its result is held until Resume (or discarded on Stop). This is what "nothing advances while paused" means; the metric is "requests started while paused" (target 0), logged per request.
2. **Stop-to-silence:** target ≤ 150 ms from the Stop action to MCI `stop` returning, measured in the silent device test (silent WAV through winmm) and logged on each Stop as `stop_latency_ms`.
3. **Controller tests** (deterministic, mock + silent clock): T17 stop with a full voice queue → zero plays after Stop, queued turns in residue; T18 pause while the writer is ahead → zero requests started while paused; T19 a new controller on an existing session dir starts in `ready`, never plays; T20 adaptive pre-roll waits for the deficit; T21 underrun logged as undesigned wait; T22 retry then fail, no substitution; T23 non-WAV voice reply refused; T24 per-performer adapters and records.

### N5 — usable alone
1. `START-DEBATE.bat`: finds Python (`py -3` or `python`), says in plain words if missing; starts the server if port 4797 is free; opens the page.
2. **Health strip** at the top of the page, one action per red item: model sources (green / offline / needs key: shows the variable name), Line Machine voices (up / **Start voices** button, which runs the Line Machine hidden), player (Windows MCI ready).
3. **Defaults preselected** from what is live: A = first working free model of the first green source, B = the same model (owner rule: same model for both performers first), voices `en_david` / `en_zira` for English, `amir` / `mana` for Persian; mode = silent rehearsal until he picks "Audible".
4. Run-book cards keyed to what he sees: "nothing happens after Start", "red source", "voice missing", "pause didn't stop the sound", each with one action.

## Revision 3 — fixes after R1 round 2 (N5 partly; notes on N1–N4)

1. **N1 pre-roll vs lookahead.** Before playback starts, the writer and voicer may run past `lookahead` up to the whole debate (the pre-roll is the buffer). `need_s` is clamped to what the remaining turns can actually supply, so the pre-roll can always be met before `max_preroll_s`. After playback starts, `lookahead` (default 3 turns ahead of what is heard) applies again. T20 covers a deficit larger than 3 turns.
2. **N5 audible by default for a human launch.** The page opened by `START-DEBATE.bat` defaults to **Audible** (Windows speakers through MCI). "Silent rehearsal" is an explicit choice on the page and the default only for automated tests and agent runs (`--silent` / API flag). The silent-AV rule binds agents: any audible run by an agent waits for the owner's permission for that occasion and is recorded as pending.
3. **N5 no fragile console window.** The .bat starts the server hidden (`pythonw` / `start /min` fallback) and opens the page; the page has a **Stop server** button and shows "server running in the background" in the health strip. Closing the browser tab does not stop sound; Stop and Stop server do.
4. **N5 run-book written in the build** (`docs/RUNBOOK.html`), one card per symptom: (1) double-click → first heard turn, what you should see/hear; (2) "Preparing… never starts"; (3) a red source; (4) "voices: down" → Start voices; (5) "Pause didn't stop the sound"; (6) experiment knobs (lookahead, pre-roll, effort, models).
5. **N4 pending near-final checks recorded explicitly** in `docs/HOST_ACCEPTANCE.md`: (a) audible pause freezes the sound, (b) audible stop silences with no stray turn, (c) six-turn audible audition. Each has pass/fail criteria and the permission trigger; none is claimed passed by an agent.
6. **N3 voice checks on the page.** Before Start the page synthesizes one short test line per chosen voice *silently* (no playback) and marks the voice green only if the reply is WAV and `speech_like`; a voice whose engine returns non-WAV or silence is marked red. A language mismatch (Persian voice with English debate language, or the reverse) shows a warning.
7. **N2 9router lanes and the key.** The OpenCode Zen free and Cline free lanes need the 9router key (`HERMES_CUSTOM_ROUTER_API_KEY`), which is not in the user environment. The agent does not read or wire the Hermes secrets file itself (credentials stay the owner's decision): the lanes show "needs key" with the variable name, and `config/sources.json` supports `key_file` so the owner can point it at the file in one line. Kilo free (no key) and any running local server work without it.

## Not in this design (later)

Studio integration, Audial OSC transport, Kokoro, designed endings, voice fragmentation (owner said: do not build on own initiative).
