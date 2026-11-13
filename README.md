# Agentic-Debate-Playground

**Audible Debate of Agents: v0 prototype.** Two AI performers (A, *The Examiner*, and B, *The Reframer*) debate one human-chosen question in alternating turns. Each turn is spoken in its own synthetic voice under a hard audible ceiling. A human controls Start / Pause / Resume / Stop / Save / New / Quit.

This repo is the **standalone, clean-boundary proof** that the handoff (v0.1, 2026-11-13) calls for when no verified Mozare Studio extension point is available. It does not replace Studio, Audial, Core or Hermes. **Next operator: [`docs/HANDOFF_JIMMY.md`](docs/HANDOFF_JIMMY.md)** (host handoff). See [`docs/REVIEW_PACKET.md`](docs/REVIEW_PACKET.md) for what is verified, what is proposal, and what remains open.
[`docs/AESTHETICS_AND_METHOD.md`](docs/AESTHETICS_AND_METHOD.md) shows how design choices are drawn from the Mozare Wiki: the practice, methods and aesthetics context. The wiki is read-only from here.

> **Status (v1, 2026-11-14, host):** ready to use on the Windows host. Text is written ahead of the voices (no dead air after a short announced pre-roll), each performer has its own free model, thinking effort and source, voices come from the Line Machine, and Pause/Stop act on the real Windows sound device. Verified silently (live free model + real voices on a silent clock; the sound device with silent audio). The audible listening check is the owner's: see [`docs/RUNBOOK.html`](docs/RUNBOOK.html) cards 1 and 5.

## Use it (no commands)

1. Double-click **`START-DEBATE.bat`**. A page opens at http://127.0.0.1:4797/.
2. Check the green dots (models, voices, speakers). Press **Start**.
3. Pause / Resume / Stop at the top. Every debate is saved in `sessions/`.

Run-book: [`docs/RUNBOOK.html`](docs/RUNBOOK.html). Host evidence: [`docs/HOST_ACCEPTANCE.md`](docs/HOST_ACCEPTANCE.md), [`docs/HOST_REUSE_MAP.md`](docs/HOST_REUSE_MAP.md). Design and its independent checks: [`docs/reality/`](docs/reality/).

### How the timing works (owner's idea, OM-907)

```
writer  ── text A, B, A … ──▶  voicer ── WAV in order ──▶  player
(free models, as fast        (Line Machine,               (waits for a pre-roll sized from
 as they answer)              ceiling enforced)             measured speeds, then plays turn
                                                            after turn with the designed gap)
```
`Turns written ahead = 0` (or `--lookahead 0`) gives the old serial behaviour.

### Model sources
`config/sources.example.json` lists every free source (Kilo free, OpenCode Zen free and Cline free via 9router, LM Studio, Ollama). Copy it to `config/sources.json` to change it locally; keys come from an environment variable or a `key_file`, never from the repo. `python -m audible_debate sources` prints what is live now.

## Command line (developers)

Requires Python ≥ 3.10 and nothing else (stdlib only).

```bash
# rehearsal: deterministic mock performers + silent placeholder audio (both clearly labelled in every record)
python -m audible_debate run --mock --speechless
#   then type: start | pause | resume | stop | save | new | quit   (or g p r s v n q; empty line = status)
#   live dramaturgy: `cue <technique> [note]` constrains the next turn only; `hold <s>` adds designed silence

# fast compressed-clock rehearsal (TEST CLOCK ONLY, never an audio speed-up)
python -m audible_debate run --mock --speechless --time-scale 0.01 --start-now --exit-when-done

# find a local OpenAI-compatible server (e.g. LM Studio). Read-only GET /v1/models
python -m audible_debate discover http://localhost:1234

# live text with the real model, still placeholder audio
python -m audible_debate run --endpoint http://localhost:1234 --model <id from discover> --speechless

# real voice through Piper (unverified on the target host), played via ffplay/aplay/afplay
python -m audible_debate run --endpoint ... --model ... --piper-model voices/en_US-xxx.onnx --audible

# source asymmetry (Direction 2 switch): only A receives excerpts
python -m audible_debate run --mock --speechless --sources-a examples/sources_a.json

# after a run
python -m audible_debate verify  sessions/<id>   # replay events through the FSM, check WAV hashes + ceiling
python -m audible_debate metrics sessions/<id>   # descriptors: opponent reference, lexicon, concessions

python -m unittest discover -s tests -v          # 23 offline tests: no model, no network, no voice
```

## Layout

| Module | Role |
|---|---|
| `audible_debate/fsm.py` | Pure lifecycle state machine (idle, ready, running, paused, stopped, completed, error, closed) |
| `audible_debate/personas.py` | Performer cards and candidate prompts (**proposal**) |
| `audible_debate/schema.py` | JSON reply cue, validation, speech cleaning, sentence-boundary trimming |
| `audible_debate/knowledge.py` | Per-agent knowledge profile: `none`, `curated` (`search` is deliberately unimplemented) |
| `audible_debate/llm.py` | `MockAdapter` and `OpenAICompatAdapter` (no hardcoded endpoint, no silent fallback) |
| `audible_debate/audio.py` | Speechless/Piper TTS, WAV-measured duration, `ClockPlayer` / `ProcessPlayer` |
| `audible_debate/controller.py` | Alternating-turn worker, epoch cancellation, ceiling enforcement |
| `audible_debate/store.py` | Append-only `sessions/<id>/` (config, events, prompts, turns, audio, residue, transcript, audit, manifest) |
| `audible_debate/metrics.py` | Run descriptors (incl. smoothing flags, convergence, designed vs undesigned silence) and replay verification |

`sessions/` is git-ignored: performance records are local source artifacts. They are not published or ingested anywhere automatically.
