"""The four-scan state machine — LLD-v1 §11.1. Pure, so exhaustive is cheap."""
import pytest

from app.models.access import PassState, ScanAction
from app.services.access.state_machine import (
    TRANSITIONS,
    IllegalTransition,
    TransitionRefusal,
    apply,
    auto_action,
    legal_actions,
)

# The spec's table, restated independently of TRANSITIONS so a typo in the
# machine cannot also be a typo in its test.
EXPECTED = {
    (PassState.NOT_ARRIVED, ScanAction.CHECK_IN): PassState.INSIDE,
    (PassState.INSIDE, ScanAction.EXIT_BREAK): PassState.ON_BREAK,
    (PassState.ON_BREAK, ScanAction.RE_ENTRY): PassState.INSIDE,
    (PassState.INSIDE, ScanAction.CHECK_OUT): PassState.DEPARTED,
    (PassState.ON_BREAK, ScanAction.CHECK_OUT): PassState.DEPARTED,
}


@pytest.mark.parametrize(("state", "action"), sorted(EXPECTED, key=str))
def test_every_legal_transition_lands_where_the_spec_says(state, action):
    assert apply(state, action, breaks_used=0, max_breaks=1).to_state == EXPECTED[(state, action)]


@pytest.mark.parametrize("state", sorted(PassState, key=str))
@pytest.mark.parametrize("action", sorted(ScanAction, key=str))
def test_every_other_pair_is_refused(state, action):
    """Exhaustive over the whole 5x6 grid — including DENIED and
    MANUAL_OVERRIDE, which are recorded outcomes and never transitions."""
    if (state, action) in EXPECTED:
        return
    with pytest.raises(IllegalTransition):
        apply(state, action, breaks_used=0, max_breaks=1)


def test_a_normal_pass_is_scanned_exactly_four_times():
    state, breaks = PassState.NOT_ARRIVED, 0
    for action in (
        ScanAction.CHECK_IN,
        ScanAction.EXIT_BREAK,
        ScanAction.RE_ENTRY,
        ScanAction.CHECK_OUT,
    ):
        transition = apply(state, action, breaks_used=breaks, max_breaks=1)
        state, breaks = transition.to_state, transition.breaks_used

    assert state is PassState.DEPARTED
    assert breaks == 1


def test_a_pass_with_no_break_is_scanned_twice():
    checked_in = apply(PassState.NOT_ARRIVED, ScanAction.CHECK_IN, breaks_used=0)
    out = apply(checked_in.to_state, ScanAction.CHECK_OUT, breaks_used=checked_in.breaks_used)
    assert out.to_state is PassState.DEPARTED
    assert out.breaks_used == 0


def test_exit_break_is_refused_once_the_break_allowance_is_spent():
    with pytest.raises(IllegalTransition) as exc:
        apply(PassState.INSIDE, ScanAction.EXIT_BREAK, breaks_used=1, max_breaks=1)
    assert exc.value.reason is TransitionRefusal.BREAK_LIMIT_REACHED


def test_the_break_allowance_is_configurable_not_hardcoded():
    transition = apply(PassState.INSIDE, ScanAction.EXIT_BREAK, breaks_used=1, max_breaks=2)
    assert transition.to_state is PassState.ON_BREAK
    assert transition.breaks_used == 2


def test_legal_actions_drop_exit_break_at_the_limit():
    assert legal_actions(PassState.INSIDE, breaks_used=0, max_breaks=1) == (
        ScanAction.EXIT_BREAK,
        ScanAction.CHECK_OUT,
    )
    assert legal_actions(PassState.INSIDE, breaks_used=1, max_breaks=1) == (
        ScanAction.CHECK_OUT,
    )


@pytest.mark.parametrize("state", [PassState.DEPARTED, PassState.REVOKED])
def test_terminal_states_offer_nothing(state):
    assert legal_actions(state, breaks_used=0, max_breaks=1) == ()
    assert TRANSITIONS[state] == {}


def test_revoked_is_refused_with_its_own_reason():
    """A revoked pass and a departed pass are both "no", but the volunteer
    does different things about them."""
    with pytest.raises(IllegalTransition) as revoked:
        apply(PassState.REVOKED, ScanAction.CHECK_IN, breaks_used=0)
    assert revoked.value.reason is TransitionRefusal.PASS_REVOKED

    with pytest.raises(IllegalTransition) as departed:
        apply(PassState.DEPARTED, ScanAction.CHECK_IN, breaks_used=0)
    assert departed.value.reason is TransitionRefusal.ALREADY_DEPARTED


def test_check_in_is_the_only_action_applied_without_asking():
    assert auto_action(legal_actions(PassState.NOT_ARRIVED, breaks_used=0)) is ScanAction.CHECK_IN


def test_check_out_is_never_applied_automatically_even_when_it_is_the_only_option():
    """A pass that has used its break has CHECK_OUT as its sole legal action.
    Auto-applying it would mark someone departed for walking past a scanner."""
    actions = legal_actions(PassState.INSIDE, breaks_used=1, max_breaks=1)
    assert actions == (ScanAction.CHECK_OUT,)
    assert auto_action(actions) is None


def test_re_entry_still_asks_because_check_out_shares_the_card():
    actions = legal_actions(PassState.ON_BREAK, breaks_used=1, max_breaks=1)
    assert set(actions) == {ScanAction.RE_ENTRY, ScanAction.CHECK_OUT}
    assert auto_action(actions) is None
