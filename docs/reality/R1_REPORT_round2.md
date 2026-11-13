# R1 report (round 2): does DESIGN.md (with "Revision 2") meet needs N1-N5?

Independent reader: Sonnet 5.5 subagent (fresh, R1 round 2). Read: REALITY.json (frozen needs), DESIGN.md in full, R1_REPORT_round1.md, HANDOFF_JIMMY.md context, controller.py and audio.py (ClockPlayer, SpeechlessTTS, measure_wav).
Judged on what the design commits to. Not knowable from files: real free-model rate limits, whether every Line Machine voice returns WAV, which Zen/Cline ids end in `-free`.

## Round 1 findings: all addressed
Every round-1 fix item has a concrete counterpart in "Revision 2", except the small ones listed under "Remaining fixes".

## Verdicts
| Need | Verdict |
|---|---|
| N1 no dead air | met (one spec clarification required in build) |
| N2 per-performer source/model/effort | met |
| N3 Line Machine voices | met |
| N4 control on Windows | met |
| N5 ready to use | partly |

## N1 No dead air
1. Part: DESIGN.md "N1" (writer/voicer/player, lookahead) plus Revision 2 N1: adaptive pre-roll `need_s = max(0,(P-H) x turns_left)` from measured P and H, visible countdown, `preroll_capped` event, silent rehearsal mode, retry policy (2 retries, backoff 2 s / 6 s, Retry-After up to 20 s, no model substitution), metrics `preroll_s`, `undesigned_wait_s`, `max_underrun_after_first_s`.
2. Walk: 6-turn run in silent rehearsal with a real free model and real Line Machine synthesis. Writer runs ahead; player waits for the formula; page counts down; per-turn underrun after the gap is read from metrics. That is the probe and metric, measured with no sound. Observed numbers (write 5.8-16.6 s, voice 0.8-4.2 s, 17 words about 6 s of audio) show P often exceeds H, so a dead-air-free run needs a long pre-roll; the design admits this (cap 90 s, `preroll_capped`, "underruns likely") instead of hiding it. Honest and measurable.
3. Failure risks: (a) Interplay gap: `lookahead` (default 3) caps how far ahead the writer may go of what has been heard; before playback starts nothing is heard, so at most 3 turns can be buffered. If `need_s` exceeds 3 turns of audio the pre-roll condition can never be met and always waits for the 90 s cap. Build must let the writer exceed `lookahead` until playback starts (or clamp `need_s`) and extend T20 to cover it. (b) P is write+voice, but the stages overlap, so P is overestimated (safe side, longer wait than needed). (c) A 90 s cap can still end in underruns; that is a reported miss of target, not a hidden one. (d) Empty content from some Kilo models is handled by retry then error.
4. Words only: nothing. `lookahead=0, preroll=1` is a labelled v0 comparison.
5. Verdict met: concrete mechanism and silent measurement; audible run recorded as pending near-final check.

## N2 Per-performer source / model / effort
1. Part: Revision 2 N2: `config/sources.json` with `free` rule per source (all / suffix list / allow list, pricing metadata wins, reason shown per model), `key_env` / `key_file`, parallel discovery with 4 s timeout and offline reason, `chosen_model` vs `served_model` with `router: true` exemption and `served_mismatch` flag, effort mapping, no substitution.
2. Walk: open page, sources listed (offline and needs-key rows visible), choose source+model+effort for A and B, Start, open `config.json` and turn records: chosen and served both stored; mismatch counted only for non-router ids. Defaults need 0 clicks; two different sources about 6 clicks. Mid-session failure goes to `error` naming the performer's model, New offered.
3. Failure risks: (a) The 9router key is not in the user environment, so Zen/Cline lanes show "needs key" until `key_file` points at the Hermes env file; the builder must preconfigure that default path, not the owner. Kilo free needs no key so the default path works. (b) Suffix rule for Zen/Cline is unverified; mislabel risk is reduced by pricing metadata and the shown reason. (c) A non-technical owner cannot act on "set variable NAME"; acceptable because defaults avoid it.
4. Words only: nothing.
5. Verdict met.

## N3 Line Machine voices
1. Part: Revision 2 N3: `LineMachineTTS` over `/api/speak`, live `/api/voices`, RIFF/WAVE check (`TTSError`), no `save`, per-turn `tts_engine`, `voice`, `sample_rate`, `channels`, `duration_s`, `rms`, `peak`, `speech_like`; `verify` counts over-ceiling (must be 0) and speech_like turns.
2. Walk: pick two voices, Start, each turn's WAV is measured from the final file, `verify` prints turns over ceiling and the speech_like count; placeholder turns are `speech_like=false`, so silence cannot pass for speech. Matches the probe and metric, silent.
3. Failure risks: (a) cloud voices that return mp3 are refused, so some listed voices will be unusable; the page should mark them when listing. (b) Voice/language mismatch warning and a preview button from round 1 were not carried into Revision 2 (optional; a preview is audible and needs his own click). (c) `speech_like` proves non-silence, not intelligibility; the audible check stays pending.
4. Words only: nothing.
5. Verdict met.

## N4 Control on Windows
1. Part: DESIGN.md "N4" (MciPlayer on a command thread, epoch bump, start in `ready`) plus Revision 2 N4: "paused" redefined as no new request starts (in-flight results held), `stop_latency_ms` target 150 ms against MCI `stop`, tests T17 (stop with full queue), T18 (pause while writer ahead), T19 (restart in `ready`).
2. Walk: silent WAV through winmm, pause/resume/stop, read device state and timing; controller tests with mock writer and silent clock cover stop and pause during speech and generation. All silent, all in the probe. Metrics (turns heard after Stop, requests started while paused, stop latency) are defined and logged.
3. Failure risks: (a) The silent test proves MCI device state, not that real sound freezes; the audible freeze check is not named as pending in the N4 text (only the general audible-run note in N1.3). Build must record it explicitly as a pending near-final check. (b) Odd WAV formats from Line Machine under MCI: covered by the WAV check plus running a real Line Machine WAV through the silent device test.
4. Words only: nothing.
5. Verdict met.

## N5 Ready to use
1. Part: Revision 2 N5: `START-DEBATE.bat` (Python check in plain words, reuses a running server), health strip with one action per red item, preselected defaults, symptom-keyed run-book cards, pre-roll countdown.
2. Walk: double-click, page opens, health green, defaults preselected, press Start, countdown, first turn. It breaks at the end: Revision 2 N1.3 makes silent rehearsal the default "until he picks Audible". The need and probe end at the first heard turn. A non-technical owner who double-clicks and presses Start would hear nothing and not know why; card 1 would not reach its goal.
3. Failure risks: (a) Default-mode contradiction above. The silent-AV rule binds agents, not him pressing Start in his own page; the human launch path should default to Audible (his Start click is the permission) and silent rehearsal should be an explicit option or a flag used by agent tests. (b) Closing the black console window kills the server and sound, unexplained (round 1 item d not addressed); the page or card must say "keep that window open, minimise it", or the .bat should start the server hidden with a Stop-server button. (c) Key-needing sources show a variable name he cannot act on; fine while Kilo works keylessly. (d) Time to first heard turn includes up to 90 s pre-roll; the countdown means it does not look like a hang.
4. Words only: run-book card text is promised but unwritten; it must exist at build with the stated symptoms.
5. Verdict partly.

## Remaining fixes (build must do; write into DESIGN.md or tests)
1. N5: default to Audible when launched by a human via the .bat; silent rehearsal only by explicit choice or agent flag. An agent's audible run stays pending owner permission.
2. N5: handle the console window (hidden start with Stop-server button, or a bold "keep this window open" line on the page and card 1).
3. N1: pre-roll may buffer beyond `lookahead` before playback starts (or clamp `need_s`); extend T20 to a deficit larger than 3 turns.
4. N4: record the audible pause/stop freeze check explicitly as a pending near-final check.
5. N3: mark voices whose engine returns non-WAV on the page; optional language-mismatch warning.
