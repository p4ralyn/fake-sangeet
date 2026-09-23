"""`json_safe` — the coercion that keeps an audit row from failing a scan."""
import json
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from app.services.audit import json_safe

DEVICE_ID = UUID("01a0cfe0-08ac-7112-b52a-176c7a23f6e0")


class Colour(Enum):
    SAFFRON = "SAFFRON"


def test_a_uuid_becomes_a_string():
    """The exact value that took the gate down: a device_id straight off the
    scanner, sitting where a caller would naturally put it."""
    assert json_safe({"device_id": DEVICE_ID}) == {"device_id": str(DEVICE_ID)}


def test_primitives_are_left_exactly_as_they_are():
    payload = {"count": 3, "ok": True, "ratio": 1.5, "note": "text", "nothing": None}
    assert json_safe(payload) == payload


def test_it_reaches_all_the_way_down():
    """A shallow pass would miss the common shape — an id inside a list inside
    a dict — and the failure only shows up later, at the INSERT."""
    assert json_safe({"scans": [{"ids": (DEVICE_ID,)}]}) == {"scans": [{"ids": [str(DEVICE_ID)]}]}


def test_enums_and_times_survive_too():
    moment = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    assert json_safe(Colour.SAFFRON) == "SAFFRON"
    assert json_safe(moment) == str(moment)


def test_something_unknown_is_recorded_rather_than_raising():
    """The deliberate trade: an audit row is a note about something that
    already happened, so a value that will not serialise must not fail the
    action it describes. It still has to leave a trace, not vanish."""

    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque>"

    assert json_safe({"thing": Opaque()}) == {"thing": "<Opaque>"}


def test_the_whole_payload_is_json_encodable_afterwards():
    """The property that actually matters, asserted against the encoder that
    does the real work rather than against our own expectations of it."""
    payload = {
        "device_id": DEVICE_ID,
        "when": datetime.now(UTC),
        "colour": Colour.SAFFRON,
        "seats": {1, 2},
    }
    json.dumps(json_safe(payload))  # raises if anything was missed
