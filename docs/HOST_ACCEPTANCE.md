# Host acceptance (handoff §4.4) — 2026-11-14

Raw evidence lives in `sessions/<id>/` on the host (not committed: recordings stay local). Agents may not play sound without the owner's permission for that occasion, so every check below was run **silently**; the audible checks are listed as **pending** with their pass/fail criteria.

| # | Check | How it was run | Result | Evidence |
|---|---|---|---|---|
| 1 | Six-turn live-generated audition, continuous, alternating, human-started | page API, `kilo/nvidia/nemotron-3-ultra-550b-a55b:free` effort low for both, Line Machine `en_david` / `en_zira`, **silent clock** | completed; A,B,A,B,A,B; started only on Start | `sessions/20261113T210447Z-7f7b8b` |
| 2 | Measured duration per turn ≤ ceiling | `verify` | 28.2 / 37.4 / 28.7 / 43.6 / 25.2 / 40.7 s, ceiling 45 s; 0 over; all hashes match | same |
| 2b | No dead air (the owner's text-ahead idea) | `metrics` | pre-roll 22.3 s, **max gap after turn 1: 0.0 s**. Write times 8.0 / 13.8 / 16.6 / **40.6** / 12.0 / 13.9 s: the serial v0 would have left that much silence before each turn | same |
| 2c | Persian | page API (UTF-8), 4 turns, `amir` / `mana` | completed; pre-roll 40.2 s, max gap after turn 1: 0.0 s; 4/4 real speech | `sessions/20261113T211400Z-77a253` |
| 3 | Pause during speech freezes audio; pause during generation | real Windows device (MCI) with **silent** WAVs through the page API; controller tests T02, T05, T18 | device position frozen 1.61 → 1.61 s over 1.5 s; no model/voice request started while paused | `sessions/20261113T210912Z-50fbee`, `tests/` |
| 4 | Stop during speech and during generation: no stray speech | same; T03, T04, T17 | stop 42.9 ms (device 42.2 ms); 3 queued turns never played, kept as `generated_not_heard` residue | same |
| 5 | Crash/restart never autoplays | T19 + design: a new server/controller starts in `ready`, nothing runs until Start | pass (tests) | `tests/test_pipeline.py` |
| 6 | Mock control-only session, labelled | page API with the offline rehearsal script + silent placeholder | labelled MOCK and SILENT PLACEHOLDER on the page and in records | `sessions/20261113T210912Z-50fbee` |

Tests: `python -m unittest discover -s tests -v` → 40 tests, all pass (23 cloud + 17 new; the 2 Windows-device tests skip off Windows).

## Pending: audible checks (need the owner's permission for the occasion)
| Check | Pass if | Fail if |
|---|---|---|
| A. Six-turn audible audition (run-book card 1) | voices heard in turn with only the designed silence between them; no clipped words | silence longer than the gap between turns, or a voice cut off |
| B. Pause freezes the sound (card 5) | sound stops mid-word on Pause and continues from there on Resume | sound keeps going after Pause |
| C. Stop silences, no stray turn (card 5) | silence at once; nothing plays afterwards | any voice after Stop |
Trigger: the owner double-clicks START-DEBATE and presses Start himself, or gives an agent permission for one named session.

## Resource use
No GPU on this host. Line Machine Persian voices: 1.3–9.3 s per turn on CPU; English 0.4–1.2 s. Free model write times observed: 7.9–40.6 s per turn.

## Failures observed (reported as failures)
- A Persian test sent from the command line arrived as `????` (Windows command-line encoding in the *test tool*, not the product). The model said the question was unreadable. Re-run through a UTF-8 client: correct. The page sends UTF-8.
- `metrics` crashed on Persian turn files (read without UTF-8 on Windows). Fixed.
- Two Kilo free models returned no content to the debate prompt; the adapter retries the same model, then reports the error (never substitutes).
