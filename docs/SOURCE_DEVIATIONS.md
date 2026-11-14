# Source deviations (handoff §2.1)

**Not done: the original sources are not available on this host to this session.** The owner's original handoff (with its tables, diagrams, prompts, T01–T14 table and §11 matrix) and the two notebook photographs were not found in the repository, and no path to them was given. Nothing was "fixed" from memory.

What would close this: the owner points to the original handoff file and the two photos; then each reconstructed part (`docs/REVIEW_PACKET.md` §2/§4, `audible_debate/personas.py`) is diffed against them and every difference is listed here before any code change.

Changes made in this session that touch reconstructed parts:
- `personas.py` speaking rates: A 2.3 → 2.64 w/s, B 2.6 → 2.54 w/s, measured from live Line Machine audio (`en_david`, `en_zira`, 6-turn run). Persona wording unchanged.
- The cloud's "v0 does not pre-generate" default was replaced by the owner's text-ahead decision of 2026-11-14 (OM-907). `lookahead=0` still reproduces v0.
