"""Pure performance lifecycle state machine.

No I/O, no threads, no clocks. The controller asks this module whether an
operator action is legal in the current state and what state follows.

States
------
idle       nothing configured yet
ready      question, performers and voices are set; waiting for the human Start cue
running    the debate is advancing (phase: generating / synthesizing / playing / gap)
paused     audible timeline and turn advancement are frozen
stopped    halted by the operator; queued work invalidated (epoch bumped)
completed  the turn ceiling was reached or a performer ended the scene
error      a component failed; nothing advances until the operator decides
closed     the operator quit; terminal

Save is an action, not a state: it is legal everywhere except idle/closed and
never changes state. Every operator action is recorded as an event by the
controller, so the lifecycle can be replayed from the event log.
"""

from __future__ import annotations

IDLE = "idle"
READY = "ready"
RUNNING = "running"
PAUSED = "paused"
STOPPED = "stopped"
COMPLETED = "completed"
ERROR = "error"
CLOSED = "closed"

STATES = (IDLE, READY, RUNNING, PAUSED, STOPPED, COMPLETED, ERROR, CLOSED)

# action -> {from_state: to_state}
TRANSITIONS: dict[str, dict[str, str]] = {
    "configure": {IDLE: READY, READY: READY},
    "start": {READY: RUNNING},
    "pause": {RUNNING: PAUSED},
    "resume": {PAUSED: RUNNING},
    "stop": {RUNNING: STOPPED, PAUSED: STOPPED},
    "complete": {RUNNING: COMPLETED},
    "fail": {RUNNING: ERROR, PAUSED: ERROR},
    "new": {READY: READY, STOPPED: READY, COMPLETED: READY, ERROR: READY},
    "save": {s: s for s in (READY, RUNNING, PAUSED, STOPPED, COMPLETED, ERROR)},
    "quit": {s: CLOSED for s in STATES if s != CLOSED},
}

# Actions that invalidate any queued or in-flight work.
EPOCH_BUMPING = frozenset({"stop", "new", "quit", "fail"})


class IllegalTransition(Exception):
    def __init__(self, state: str, action: str):
        super().__init__(f"action '{action}' is not allowed in state '{state}'")
        self.state = state
        self.action = action


def allowed_actions(state: str) -> list[str]:
    return [a for a, table in TRANSITIONS.items() if state in table]


SYSTEM_ACTIONS = frozenset({"configure", "complete", "fail"})


def operator_actions(state: str) -> list[str]:
    return [a for a in allowed_actions(state) if a not in SYSTEM_ACTIONS]


def transition(state: str, action: str) -> str:
    table = TRANSITIONS.get(action)
    if table is None or state not in table:
        raise IllegalTransition(state, action)
    return table[state]


def replay(actions: list[str], initial: str = IDLE) -> str:
    """Fold a list of accepted actions into a final state (raises on illegal)."""
    state = initial
    for a in actions:
        state = transition(state, a)
    return state
