# Agentic-Debate-Playground

**Audible Debate of Agents: v0 prototype.** Two AI performers (A, *The Examiner*, and B, *The Reframer*) debate one human-chosen question in alternating turns. Each turn is spoken in its own synthetic voice under a hard audible ceiling. A human controls Start / Pause / Resume / Stop / Save / New / Quit.

This repo is the **standalone, clean-boundary proof** that the handoff (v0.1, 2026-11-13) calls for when no verified Mozare Studio extension point is available. It does not replace Studio, Audial, Core or Hermes. See [`docs/REVIEW_PACKET.md`](docs/REVIEW_PACKET.md) for what is verified, what is proposal, and what remains open.
[`docs/AESTHETICS_AND_METHOD.md`](docs/AESTHETICS_AND_METHOD.md) shows how design choices are drawn from the Mozare Wiki: the practice, methods and aesthetics context. The wiki is read-only from here.

> Status: Gate 1 (text-only, mock, event log, human controls) is complete and tested. The Gate 2 scaffolding (measured-duration ceiling, speechless TTS, Piper adapter, CLI player) exists, but **no live model or real voice has been run yet**.

## Quick start

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
