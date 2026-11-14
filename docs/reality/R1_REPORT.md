# R1 report (round 3): does DESIGN.md (with "Revision 3") meet needs N1-N5?

Independent reader: Sonnet 5.5 subagent (fresh, R1 round 3). Read: REALITY.json (frozen needs), DESIGN.md in full, R1_REPORT_round2.md.
Judged on what the design commits to. Not knowable from files: real free-model rate limits, whether every Line Machine voice returns WAV, which Zen/Cline ids end in `-free`, whether `pythonw` is on the owner's Python install.

## Round-2 fixes against Revision 3
| # | Round-2 fix | Revision 3 item | Status |
|---|---|---|---|
| 1 | N5 default Audible for human launch; silent only by choice or agent flag | item 2 (agents' audible runs wait for owner permission) | addressed |
| 2 | N5 console window | item 3 (hidden start, Stop server button, "running in background" in health strip) | addressed; one small gap, see N5 |
| 3 | N1 pre-roll beyond lookahead; T20 for deficit > 3 turns | item 1 (buffer may run past lookahead before playback; need_s clamped; T20 extended) | addressed |
| 4 | N4 audible freeze/stop check recorded as pending | item 5 (`docs/HOST_ACCEPTANCE.md` a/b/c with pass/fail and permission trigger) | addressed |
| 5 | N3 mark non-WAV voices; language warning | item 6 (silent per-voice test line before Start, red if non-WAV or silent; mismatch warning) | addressed |
| - | N2 key (open note from round 2) | item 7 (lanes show "needs key"; `key_file` one-line option; agent does not read secrets) | addressed, respects the credential rule |

## Verdicts
| Need | Verdict |
|---|---|
| N1 no dead air | met |
| N2 per-performer source/model/effort | met |
| N3 Line Machine voices | met |
| N4 control on Windows | met |
| N5 ready to use | met (small build conditions below) |

## N1 No dead air
1. Part: N1 pipeline + Revision 2 N1 (adaptive pre-roll, countdown, silent rehearsal, retry) + Revision 3 item 1 (buffer past lookahead before playback, need_s clamped).
2. Walk: 6-turn silent rehearsal, real free model, real Line Machine synthesis, silent clock. Writer and voicer run ahead past lookahead until the pre-roll is met; countdown on the page; read `undesigned_wait_s` and `max_underrun_after_first_s`. The round-2 deadlock (need above 3 turns never met) is closed: the clamp makes the condition reachable.
3. Risks: P overestimated (safe side, longer wait); with P 6-17 s vs H about 8 s the pre-roll can reach the 90 s cap, then `preroll_capped` reports likely underruns (a visible miss, not hidden); after playback starts, lookahead 3 must absorb a 17 s write.
4. Words only: nothing.
5. Verdict met: formula from measured P and H, event `preroll_capped`, T20 deficit test, per-turn underrun metric. Audible run stays pending owner permission.

## N2 Per-performer source / model / effort
1. Part: Revision 2 N2 + Revision 3 item 7.
2. Walk: page lists live sources (offline and needs-key rows visible), A and B picked separately (about 6 clicks, 0 for defaults), Start, `config.json` and turns hold `chosen_model` and `served_model`; mismatch flagged except for `router: true`. Kilo free works without a key, so two different real models can be compared with no credential.
3. Risks: Zen/Cline lanes stay "needs key" until the owner points `key_file` (his decision, correct per rule); the page must give one plain sentence, not only a variable name. Suffix rule for Zen/Cline unverified, reason shown per model.
4. Words only: nothing.
5. Verdict met.

## N3 Line Machine voices
1. Part: Revision 2 N3 + Revision 3 item 6.
2. Walk: pick two voices; before Start each gets a silent test synthesis, green only if WAV and `speech_like`; Start; every turn records engine, voice, rms, peak, speech_like, duration from the final file; `verify` prints over-ceiling (must be 0) and speech_like count. Maps directly to probe and metric, silent.
3. Risks: `speech_like` is not intelligibility (audible check pending in HOST_ACCEPTANCE); voice test adds a few seconds before Start (Persian 3-4 s), acceptable.
4. Words only: nothing.
5. Verdict met.

## N4 Control on Windows
1. Part: MciPlayer + Revision 2 N4 (paused definition, stop_latency_ms <= 150, T17-T19) + Revision 3 item 5.
2. Walk: silent WAV (including a real Line Machine WAV) through winmm pause/resume/stop with device state and timing; controller tests with full queue for stop and pause during speech and generation; restart in `ready`. What silent tests cannot show (the sound really freezes) is now named as pending checks (a) and (b) with the permission trigger, not claimed.
3. Risks: none new. Stop server must stop playback first (build detail).
4. Words only: nothing.
5. Verdict met.

## N5 Ready to use
1. Part: Revision 2 N5 + Revision 3 items 2-4.
2. Walk: double-click `START-DEBATE.bat`; Python found or plain-word message; server started hidden (or reused); page opens; health strip green; defaults preselected, mode Audible; he presses Start; countdown "Preparing k of n"; first turn heard from the speakers. The round-2 break (silent default) is gone. Card 1 is specified as double-click to first heard turn, followed by five symptom cards.
3. Risks: (a) a hidden server hides a crash or port conflict; the .bat must wait for the port to answer before opening the browser, say so in plain words if it does not, and write a `server.log` the run-book names. (b) Closing the tab does not stop sound (stated); card 1 must say "reopen START-DEBATE to get the Stop button". (c) First heard turn may take up to about 90 s plus voice tests; the countdown prevents it looking hung. (d) Run-book card text is a build output with listed symptoms; the R2 fresh reader tests it.
4. Words only: run-book wording (structure specified, written in build).
5. Verdict met: every probe step has a mechanism (bat, health strip, Audible default, countdown, Stop server, symptom cards) and R2 can test it with a fresh reader.

## Remaining build conditions (not design gaps)
1. N5: .bat waits for the server to answer before opening the page; plain-words message plus `server.log` if not.
2. N5: card 1 and the page say that closing the tab does not stop sound, and how to stop it (reopen the .bat, press Stop).
3. N2: the "needs key" row gives one plain action sentence, not only a variable name.
4. N4: Stop server stops playback first, then the server.
