"""The four-scan state machine. LLD-v1 §11.1.

    NOT_ARRIVED --CHECK_IN--> INSIDE
    INSIDE --EXIT_BREAK--> ON_BREAK --RE_ENTRY--> INSIDE
    INSIDE|ON_BREAK --CHECK_OUT--> DEPARTED   (terminal)
    any --admin--> REVOKED                    (terminal, denies all scans)

Pure. No database, no clock, no settings — give it a state and it tells you
what is legal. **The server decides what is legal, never the phone.**
"""
from dataclasses import dataclass
from enum import StrEnum

from app.models.access import TERMINAL_PASS_STATES, PassState, ScanAction

DEFAULT_MAX_BREAKS = 1

# The whole machine, in one table. Everything else in this module reads it.
TRANSITIONS: dict[PassState, dict[ScanAction, PassState]] = {
    PassState.NOT_ARRIVED: {ScanAction.CHECK_IN: PassState.INSIDE},
    PassState.INSIDE: {
        ScanAction.EXIT_BREAK: PassState.ON_BREAK,
        ScanAction.CHECK_OUT: PassState.DEPARTED,
    },
    PassState.ON_BREAK: {
        ScanAction.RE_ENTRY: PassState.INSIDE,
        ScanAction.CHECK_OUT: PassState.DEPARTED,
    },
    PassState.DEPARTED: {},
    PassState.REVOKED: {},
}

# §11.1's "operator prompt" column, as data. An action the operator must choose
# is never applied automatically even when it is the only legal one: a pass that
# has used its break has CHECK_OUT as its sole legal action, and auto-applying
# that would mark someone departed for walking past a scanner.
OPERATOR_DECIDED_ACTIONS: frozenset[ScanAction] = frozenset(
    {ScanAction.EXIT_BREAK, ScanAction.CHECK_OUT}
)

# Actions that consume one of the pass's breaks.
BREAK_CONSUMING_ACTIONS: frozenset[ScanAction] = frozenset({ScanAction.EXIT_BREAK})

# Fail loudly at import if the machine and the enum ever drift apart.
assert set(TRANSITIONS) == set(PassState)
assert all(not TRANSITIONS[state] for state in TERMINAL_PASS_STATES)


class TransitionRefusal(StrEnum):
    """Why an action was refused. Distinct from the transport error code so the
    reason survives into `scans.deny_reason` verbatim."""

    PASS_REVOKED = "PASS_REVOKED"  # noqa: S105 -- refusal reason, not a secret
    ALREADY_DEPARTED = "ALREADY_DEPARTED"
    BREAK_LIMIT_REACHED = "BREAK_LIMIT_REACHED"
    ILLEGAL_FOR_STATE = "ILLEGAL_FOR_STATE"


class IllegalTransition(Exception):
    def __init__(self, reason: TransitionRefusal, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Transition:
    action: ScanAction
    from_state: PassState
    to_state: PassState
    breaks_used: int


def legal_actions(
    state: PassState, *, breaks_used: int, max_breaks: int = DEFAULT_MAX_BREAKS
) -> tuple[ScanAction, ...]:
    """What this pass may legally do next, in the order the scanner shows them."""
    actions = []
    for action, _ in TRANSITIONS[state].items():
        if action in BREAK_CONSUMING_ACTIONS and breaks_used >= max_breaks:
            continue
        actions.append(action)
    return tuple(actions)


def auto_action(actions: tuple[ScanAction, ...]) -> ScanAction | None:
    """The action to apply without asking, or None to raise ACTION_REQUIRED.

    Exactly one legal action AND that action is not operator-decided. §11.1
    marks RE_ENTRY "auto", but from ON_BREAK it shares the card with CHECK_OUT,
    so the operator still has a choice to make and still gets one.
    """
    if len(actions) == 1 and actions[0] not in OPERATOR_DECIDED_ACTIONS:
        return actions[0]
    return None


def apply(
    state: PassState,
    action: ScanAction,
    *,
    breaks_used: int,
    max_breaks: int = DEFAULT_MAX_BREAKS,
) -> Transition:
    """Validate and compute the transition. Raises `IllegalTransition`."""
    if state is PassState.REVOKED:
        raise IllegalTransition(
            TransitionRefusal.PASS_REVOKED, "This pass has been revoked. Do not admit."
        )
    if state is PassState.DEPARTED:
        raise IllegalTransition(
            TransitionRefusal.ALREADY_DEPARTED, "This pass has already checked out."
        )
    if action in BREAK_CONSUMING_ACTIONS and breaks_used >= max_breaks:
        raise IllegalTransition(
            TransitionRefusal.BREAK_LIMIT_REACHED,
            f"No breaks left ({breaks_used}/{max_breaks}). Check out instead.",
        )
    to_state = TRANSITIONS[state].get(action)
    if to_state is None:
        raise IllegalTransition(
            TransitionRefusal.ILLEGAL_FOR_STATE,
            f"{action.value} is not allowed from {state.value}.",
        )
    return Transition(
        action=action,
        from_state=state,
        to_state=to_state,
        breaks_used=breaks_used + (1 if action in BREAK_CONSUMING_ACTIONS else 0),
    )
