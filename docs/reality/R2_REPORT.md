# R2 report: independent final check (silent)

Checker: Sonnet 5.5 subagent (fresh, R2). No sound played, no browser opened, no model or voice call. I ran the unit tests, `verify`, `metrics` and `sources`, and read the session files directly.

| Need | Verdict | One line |
|---|---|---|
| N1 no dead air | met (silent) | Max underrun after turn 1 is 0.0 s in both live sessions. Pre-roll was 22.3 s (EN) and 40.2 s (FA), announced. Write times up to 40.6 s never became a gap. |
| N2 pick model, source, effort | partly | Per-performer pickers exist and chosen equals served in all 10 live turns. Only one keyless source is live (kilo-free, 11 free models). Zen and Cline need a key, LM Studio and Ollama are offline. No live run used different sources for A and B. Clicks unmeasured. |
| N3 Line Machine voices | met (silent) | 10 of 10 turns engine line-machine and speech_like; 0 over ceiling (max 43.6 s of 45). One turn was shortened and re-voiced after exceeding the ceiling (logged as residue). |
| N4 Pause and Stop | met (silent device) | Real MCI device: pause froze position, stop 42.9 ms, nothing played after stop, restart never autoplays (T17-T19, T25). Not heard through speakers; no live-model session was stopped mid-queue. |
| N5 ready to use | partly | One .bat and a card run-book exist and read well. I could not run the .bat or open the page and no fresh reader has done it, so time to first turn and questions are unmeasured. |

## Evidence
- Tests: 40 tests, OK (only ResourceWarnings from 429 handling).
- `verify` on both live sessions: ok=true, problems=[], turns_over_ceiling 0, speech_like 6/6 and 4/4. `metrics`: max_underrun_after_first_s 0.0, served_mismatches 0.
- Per turn (EN): undesigned_wait_s 22.29 (turn 1, pre-roll), then 0.0 for the other five. FA: 40.21, then 0.0 for the other three. The served model equals the chosen model without the `kilo/` prefix, router false.
- Pause and Stop: `sessions/20261113T210912Z-50fbee/events.jsonl` seq 18-22 (pause at 1.579 s, resume 1.612 s, stop at 2.805 s, stop_latency 42.86 ms, worker_cancelled).

## Still pending (not credited)
1. Audible six-turn audition (HOST_ACCEPTANCE card A): gap-free and unclipped through speakers.
2. Audible Pause freezes mid-word and Stop silences (cards B, C).
3. A fresh reader's double-click walk (N5), including cold start of the voices.
4. A live run with different sources for A and B (N2), and a keyed source once the 9router key exists.

## Defects and observations
- Session 50fbee proves stop with a mock script and silent placeholders, not a live model queue; live-queue stop rests on unit tests.
- Several fallbacks and the voices cold start rely on "tell Jimmy"; acceptable but not zero-help.
- No code defects found that I can cite by file and line.
