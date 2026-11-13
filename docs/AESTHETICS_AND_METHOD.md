# Aesthetics and method: what this prototype takes from the Mozare Wiki

The Mozare Wiki is the **context** for this work: Mohammad Zare's practice, methods and aesthetics. It is not a place this prototype writes to. I read the wiki read-only on 2026-11-13 from branch `ccr-18cb2ef2-x3dzio` and changed nothing in it.

How to read this page. There are three authority levels, and they are kept separate:

- **Owner-stated:** a dated principle in Mohammad's own words, recorded in the wiki.
- **Wiki concept:** a working definition in the wiki, usually candidate-level and source-bounded.
- **Applied here:** my design reading for *this new work*. It is a candidate relation and is not owner-approved.

Several of the owner's principles were stated for his own **sound work**, on 2026-11-11. The wiki warns against projecting them onto other works. Each use below is therefore an *application to test*, not a claim that the principle governs this piece. Please confirm or reject each one.

---

## Applied in code

| Wiki source | What it says (short) | Applied here | Where |
|---|---|---|---|
| `03-objects/concepts/prompt-as-stage-direction.md` (wiki concept) | A prompt is a cue in a responsive situation. It marks a next turn, invites a reply, organizes timing, and constrains what counts as an answer. | The per-turn message is built as a **cue**: score position (`ROUND r OF R (opening/middle/closing)`), time budget in seconds, and the opponent's last turn. The operator can place a live **`cue`** on the next turn only. | `controller._messages`, `controller.cue` |
| `03-objects/methods/token-distribution-techniques.md` (owner's two-model study) | Constraints redirect model defaults with differing reliability. Defaults such as explanation, taxonomy and meta-commentary absorb the form. | **Meta-Commentary Ban** and **Anti-Taxonomy** are added to the shared rules. Seven techniques (lateral entry, forced compression, perspective inversion, register contamination, productive contradiction, wrong tool, fragment seeding) are available as operator cues. Their effect on *spoken debate* is untested. | `personas.SHARED_RULES`, `personas.CUE_TECHNIQUES` |
| `03-objects/concepts/smoothing-machine.md` (wiki concept); constellation op. *"resisting smooth closure"*; `making-without-obligation-to-soothe.md` (**owner-stated** 2026-11-01) | Fluent continuity can hide its route. Art is not obliged to soothe or redeem. | The rule forbids ending on a reconciling summary or reassurance: agreement must be earned by a specific reason. The metrics flag closure, meta-commentary and taxonomy phrases per turn (`closing_turn_flags`), and track **convergence**, i.e. whether A and B's vocabularies merge from the first round to the last. | `personas`, `metrics.smoothing_flags`, `run_metrics` |
| `03-objects/concepts/residue.md` (wiki concept) | Material removed from an active route without erasure, with reason, status and reopening condition. Storing only accepted results produces a false genesis. | Every removed output becomes a residue record with a reason: invalid outputs, over-budget replies before regeneration, trimmed text, text cut for the ceiling, a generation that returned after Stop, and a turn interrupted mid-speech. | `store.residue`, `residue.jsonl` |
| `artistic-ruining-as-designed-process.md`, `offbeat-against-crafted-onbeat.md` (**owner-stated** 2026-11-11, for his sound work) | Ruining is artistic only when designed and sensed as ruining. An off-beat works against a well-made on-beat. | The inter-turn gap is the **pulse**. An operator **`hold`** is designed silence. Time spent waiting for the model or the synthesizer is recorded separately as `undesigned_wait_s`, with pauses excluded. `metrics.beat` counts the gaps where latency exceeded the designed silence. Crashes are always logged as failures, never as gestures. | `controller._run`, `metrics.run_metrics["beat"]` |
| `03-objects/concepts/conditioned-access.md`, `evidence-state.md`, `claim-permission.md` (wiki concepts) | Access is real but partial. A derivative must not silently replace its source. A highly certain analogy remains an analogy. | Present from v0: per-agent knowledge profiles, `verified_source_ids` vs `unverified_citations`, and the `analogy` flag. In the asymmetric condition, one performer *audibly lacks* what the other has. | `knowledge.py`, `schema.py` |
| `03-objects/concepts/scenographic-epistemology.md` (wiki concept) | Conditions of knowing can be staged. Compare several agents *without merging them into consensus*; keep replayable events. | Two personas that are never merged, no verdict, and a replayable event log. | `controller`, `metrics.verify_session` |

## Recorded but deliberately *not* implemented

- **Designed decay as an ending** (`designed-decay.md`, `decay-ending-as-crafted-layer.md`, **owner-stated** 2026-11-11). One could design how a debate ends: which alignment decays, how, and to what sensation. I have not built an ending mode. It is an artistic decision for you (see the questions below), and building it without you would be exactly the undesigned ruining the principle rules out.
- **Precise unexpected entry** (`precise-unexpected-instrument-entry.md`). A single, short, unexpected event in the middle that hands the pulse back. A designed "source encounter" cue could become one. It needs Audial, so it is deferred to Gate 3.
- **Restrained voice fragmentation** (`restrained-voice-fragmentation.md`). The principle is "slight, precise, well-performed, not extreme or weird". That matches the handoff's warning against decorative "technology-themed" effects. No voice processing is built yet. When there is, this principle should set its limit.
- **Visible genesis** (`visible-genesis.md`). It has four layers: public form, process archive, critical writing, and protected residue. The session store is the process archive. *What the audience sees* is open, and the wiki's test applies: process must change the encounter, because "a decorative provenance panel is not enough."
- **Collective enunciation** (`collective-enunciation.md`). The wiki explicitly warns that software multi-agent distribution "should not be equated with collective enunciation." This work should not be described that way unless a specific shared operation is shown.

## Precedents in the practice (wiki-recorded, not verified in code)

- **ANJA KE KHESARAT production** (`03-objects/projects/anja-ke-khesarat-production.md`, plus the 2026-11-05 owner statement): Mohammad operated text-generation engines and **OSC cue bridges** live. Audial and Visor came from that practice. This confirms an OSC-cue precedent for connecting this prototype to Audial, but the code was not available in this session. ANJA's authority order (play, then oral evidence, then direct statements, then rehearsal evidence, then interpretation, then model-generated possibilities) is a good model for ranking decisions in this work too.
- **Hamlet Pop-Upper** (`03-objects/works/hamlet-pop-upper.md`): a fixed dramaturgical score meets a questioning apparatus that changes only *how* each event is met. The fixed round structure with indeterminate speech is a similar shape. This is formal adjacency only.

## Questions this adds for the owner

1. Do the 2026-11-11 sound principles (pulse/off-beat, designed ruining, designed decay) apply to this work's *turn-taking and silence*, or only to musical material?
2. Should a debate have a **designed ending**, for example a decaying last round? Or should it simply stop at the turn ceiling?
3. Which operator cue techniques belong in the performance instrument, and which are rehearsal tools only?
4. Is a **closure flag** on the last turn a failure to redo, or material to keep and expose?
