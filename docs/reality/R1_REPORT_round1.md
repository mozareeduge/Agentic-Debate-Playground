# R1 report: does DESIGN.md meet needs N1-N5?

Independent reader: Sonnet 5.5 subagent (fresh, gate R1). Read: REALITY.json (frozen needs), DESIGN.md, HANDOFF_JIMMY.md (sections 4.3, 4.4, 6), controller.py, audio.py.
Unknowable from files (judged on what the design commits to): whether Line Machine /api/speak returns WAV for every engine; which 9router/Zen/Cline model ids carry "free"; whether Python is on PATH on his laptop; real free-model latency and rate limits.

## Verdicts

| Need | Verdict |
|---|---|
| N1 no dead air | partly |
| N2 per-performer source/model/effort | partly |
| N3 Line Machine voices | partly |
| N4 control on Windows | partly |
| N5 ready to use | partly |

No need is `not`: each has a real mechanism. None is `met` yet because each has a gap that would show up in his real situation.

## N1 No dead air
1. Part: DESIGN.md "N1" section: writer / voicer / player workers, `lookahead` 3, `preroll` 2, underrun metric, `preroll_s`, `max_underrun_after_first_s`.
2. Walk: 6-turn run. Writer writes A, B, A... as text; voicer makes WAVs; player waits for 2 voices, plays, gap, plays. Metric is read from session metrics. Works only if write + voice time per turn is at most the play time (30-120 s ceiling). Pre-roll is reported as a number after the fact, not announced before.
3. Failure risks: (a) a slow free model (60 s) plus a slow voice (20 s) is 80 s per turn, more than a 30-45 s turn; text-ahead cannot beat throughput below playback speed, so underruns persist and nothing adapts. (b) Fixed `preroll=2` is a guess, not derived from measured speed. (c) Rate limits (429) on free gateways are not mentioned: a retry wait is an undesigned wait. (d) The probe plays real Line Machine voices aloud; the silent-AV rule forbids that without his permission and the design has no silent way to take the N1 measurement. (e) Strict A->B alternation makes the writer chain serial, so lookahead only buys a buffer when writing is faster than playing.
4. Cut for words: nothing; `lookahead=0, preroll=1` v0 mode is comparison only.
5. Verdict partly.
Fixes: (1) adaptive pre-roll from measured per-turn write+voice time, with an on-page "starting in about N s" before Start finishes; if throughput is below playback speed, say so on the page and in metrics. (2) A silent rehearsal mode: real model and real Line Machine synthesis, playback replaced by a silent clock of the real WAV duration; N1 is measured with no sound; audible run only when he asks. (3) A 429/timeout policy: backoff retry on the same model, counted as writer delay, shown as "waiting on model X (rate limit)".

## N2 Per-performer source / model / effort
1. Part: DESIGN.md "N2": `config/sources.json`, live `/v1/models` discovery with free filter, per-performer `{source, model, effort}`, logged parameter-drop retry, served model stored per turn.
2. Walk: open page, lists sources (offline shown), pick for A and B, Start, `config.json` has choices and turns have the served model. Concrete; no-substitution rule is good.
3. Failure risks: (a) the free filter "id contains free" is a guess: Zen/Cline lanes via 9router may not be named that way (real free models hidden) or a paid model may contain "free" (surprise cost). (b) The 9router key comes from env var `HERMES_CUSTOM_ROUTER_API_KEY`; a double-clicked .bat may not see it, and "no key" behaviour is unspecified. (c) Routers (`kilo-auto/free`) serve a different model than chosen, so "mismatch target 0" is undefined for them. (d) No behaviour when the chosen model fails mid-session. (e) No discovery timeout or cache: the page can hang on a down gateway. (f) "Clicks to set both performers" has no budget.
4. Cut for words: nothing; effort-sized `max_tokens` is justified.
5. Verdict partly.
Fixes: (1) per-source allowlist/denylist and use gateway price metadata when present; otherwise label "free (by name)". (2) Specify no-key / no-gateway rows with a reason and fix line; short discovery timeout plus cache. (3) Record `chosen_model` and `served_model`; count mismatch only for non-router ids. (4) Mid-session failure: turn marked failed, session pauses, page offers retry or change model.

## N3 Line Machine voices
1. Part: DESIGN.md "N3": `LineMachineTTS` over `/api/speak`, `/api/voices` list, hidden start on :4795, error on Line Machine down, placeholder labelled.
2. Walk: pick voices, Start, each turn POSTs speak, WAV measured by `measure_wav`, ceiling path enforced. The probe wants the engine field and non-silent samples via `verify`; the design records only duration, and the placeholder WAVs are silent, so a duration check cannot tell speech from silence (audio.py SpeechlessTTS).
3. Failure risks: (a) cloud voices (Edge/Gemini/ElevenLabs) may return mp3, not WAV. (b) Cloud quota and latency add to N1 wait. (c) Choosing a voice unheard is guessing; a preview is audible and needs his own click. (d) A Persian text with an English voice is possible. (e) Measured length must be of the final processed file.
4. Cut for words: nothing.
5. Verdict partly.
Fixes: (1) turn record and `verify` gain `engine`, `voice`, sample peak/rms and a `speech_like` flag; placeholder turns do not count as real speech. (2) Convert or refuse non-WAV explicitly, shown on the page. (3) Optional voice preview button, pressed by him, marked audible. (4) Warn on voice/question language mismatch.

## N4 Control on Windows
1. Part: DESIGN.md "N4": `MciPlayer` (winmm via ctypes, command thread), pause gating of writer/voicer, epoch bump to residue on stop, start in `ready`.
2. Walk: silent WAV through MCI, read device status and timing after pause/resume/stop: legitimate silent proof. Controller tests with a full queue are named in the probe but the design lists no cases. Restart-never-plays is concrete.
3. Failure risks: (a) in-flight HTTP calls cannot be paused, so "work done while paused = 0" is contradicted. (b) No stop-to-silence target. (c) MCI quirks (alias per file, odd WAV formats from Line Machine). (d) The silent test cannot prove the audible sound freezes; that needs his permission at the near-final check (HANDOFF 4.4 item 3).
4. Cut for words: nothing.
5. Verdict partly.
Fixes: (1) redefine metric: no new request starts while paused; an in-flight result is held, counted, and cancelled via timeout/socket close where possible. (2) Target for stop-to-silence (for example 150 ms by MCI status) measured in the silent test. (3) List the tests: stop with full queue, pause mid-generation, pause mid-voice, restart in `ready`. (4) Mark the audible freeze check as gated by his permission.

## N5 Ready to use
1. Part: DESIGN.md "N5": `START-DEBATE.bat`, one page, pipeline strip, `docs/RUNBOOK.html` card 1.
2. Walk: double-click, server starts, browser opens, choose, Start, hear turn. Works only if every precondition holds.
3. Failure risks: (a) no Python/PATH check, a non-technical owner sees a flash and nothing. (b) Gateways or Line Machine down with no one-button fix beyond "start voices". (c) Second double-click with port 4797 busy. (d) Closing the console window kills the server and audio, unexplained. (e) Silent pre-roll looks like a hang without a countdown. (f) Run-book symptoms unspecified. (g) No preselected defaults.
4. Cut for words: nothing.
5. Verdict partly.
Fixes: (1) .bat checks Python with a plain message and reuses a running server. (2) Health strip, green/red per dependency, one action per red. (3) "Preparing, about N s" during pre-roll. (4) Defaults preselected from what is live; run-book keyed to symptoms (nothing heard, long silence, wrong voice, stuck, stopped itself), one action each.

## Top 3 fixes
1. N1/N4: make timing real: adaptive pre-roll with on-page countdown, silent rehearsal mode, a 429/timeout policy; redefine "work while paused" for in-flight calls.
2. N3/N2: make evidence checkable: record engine, voice, served model, sample rms and speech_like; refuse non-WAV; replace the name-only free filter with allowlist plus price metadata.
3. N5: health strip, Python/gateway checks in the .bat, defaults preselected, symptom-keyed run-book.
