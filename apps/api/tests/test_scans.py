"""`POST /scans` and `/scans/confirm` — LLD-v1 §11.2, §11.5.

The duplicate-suppression window (default 10 s) means a whole four-scan
lifecycle inside one test would be swallowed as double-taps. Tests that walk
the lifecycle therefore set the window to 0 explicitly, and suppression gets
its own tests with the window on. That is not a workaround — it makes the
window's exact effect visible in both directions.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.models import Order, Pass, PassState, Scan, ScanAction, ScanResult, Ticket, TicketStatus
from app.models.ticketing import PartnerMode
from app.services.passes.token import mint_pass_token

SECURITY_NET_ID = "security.test"
OWNER_NET_ID = "owner.test"


@pytest.fixture
def no_duplicate_window(monkeypatch):
    """Walk the lifecycle without §11.2 anti-passback folding it into one scan."""
    from app.core.settings import get_settings

    monkeypatch.setenv("SCAN_DUPLICATE_WINDOW_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def scanner_staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, :sec, 'SECURITY'), "
            "(gen_random_uuid(), :e, :own, 'OWNER'), "
            "(gen_random_uuid(), :e, 'other.guard', 'SECURITY')"
        ),
        {"e": event_id, "sec": SECURITY_NET_ID, "own": OWNER_NET_ID},
    )
    await db_session.flush()


@pytest_asyncio.fixture
async def gate_id(db_session, event_id):
    from app.models import Gate

    gate = Gate(event_id=event_id, name="Main Gate")
    db_session.add(gate)
    await db_session.flush()
    return gate.id


@pytest_asyncio.fixture
async def issued_pass(db_session, event_id):
    """An order, a ticket and the pass minted from it, COMMITTED.

    Committed on purpose: the ACTION_REQUIRED path releases its row lock with
    a rollback, and an uncommitted fixture would be rolled back with it.
    """
    from app.models import Event
    from app.services.passes.issue import issue_pass

    event = await db_session.get(Event, event_id)
    order = Order(
        event_id=event_id,
        external_id="sheet-row-1",
        purchaser_email="buyer@snu.edu.in",
        purchaser_name="Aarav Sharma",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    ticket = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref="FS26-0001",
        status=TicketStatus.PASS_ISSUED,
        partner_mode=PartnerMode.SELF_CLAIM,
        claim_code="CLAIM-1",
    )
    db_session.add(ticket)
    await db_session.flush()
    issued = await issue_pass(db_session, event=event, ticket=ticket)
    await db_session.commit()
    return issued


@pytest_asyncio.fixture
async def token(issued_pass, event_id):
    return mint_pass_token(
        event_id=event_id,
        pass_id=issued_pass.id,
        serial=issued_pass.serial,
        expires_at=issued_pass.expires_at,
    )


async def login(client, net_id: str) -> dict[str, str]:
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )
    return {"x-csrf-token": client.cookies.get("ug_csrf")}


async def scan(client, headers, token, *, gate_id=None, client_scan_uuid=None):
    return await client.post(
        "/api/v1/scans",
        headers=headers,
        json={
            "token": token,
            "gate_id": str(gate_id) if gate_id else None,
            "client_scan_uuid": str(client_scan_uuid or uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )


async def check_in(client, headers, token, *, gate_id=None, client_scan_uuid=None):
    """Scan, then confirm CHECK_IN.

    Every transition is operator-decided now, including the first: the ID photo
    only reaches the phone on an ACTION_REQUIRED response, so admitting someone
    without a card meant admitting them without anybody seeing the face.
    """
    presented = await scan(
        client, headers, token, gate_id=gate_id, client_scan_uuid=client_scan_uuid
    )
    assert presented.json()["status"] == "ACTION_REQUIRED", presented.text
    assert presented.json()["legal_actions"] == ["CHECK_IN"]
    return await confirm(client, headers, presented.json()["decision_token"], "CHECK_IN")


async def confirm(client, headers, decision_token, action):
    return await client.post(
        "/api/v1/scans/confirm",
        headers=headers,
        json={"decision_token": decision_token, "action": action},
    )


# ── authorisation ────────────────────────────────────────────────────────


async def test_scanning_requires_a_session(client, token):
    response = await client.post(
        "/api/v1/scans",
        json={"token": token, "client_scan_uuid": str(uuid4()),
              "occurred_at": datetime.now(UTC).isoformat()},
    )
    assert response.status_code == 401


async def test_a_visitor_cannot_scan(client, scanner_staff, token):
    headers = await login(client, "random.student")
    assert (await scan(client, headers, token)).status_code == 403


async def test_a_scan_without_a_csrf_token_is_refused(client, scanner_staff, token):
    await login(client, SECURITY_NET_ID)
    response = await scan(client, {}, token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


# ── the four-scan lifecycle ──────────────────────────────────────────────


async def test_the_full_four_scan_lifecycle(
    client, db_session, scanner_staff, gate_id, issued_pass, token, no_duplicate_window
):
    headers = await login(client, SECURITY_NET_ID)

    # 1. CHECK_IN — a card, because this is where the volunteer compares the
    #    face in front of them to the uploaded ID.
    first = await check_in(client, headers, token, gate_id=gate_id)
    assert first.status_code == 200
    assert first.json()["status"] == "DONE"
    assert first.json()["state"] == "INSIDE"
    assert first.json()["action"] == "CHECK_IN"

    # 2. EXIT_BREAK — the operator chooses between break and check-out.
    second = await scan(client, headers, token, gate_id=gate_id)
    body = second.json()
    assert body["status"] == "ACTION_REQUIRED"
    assert body["legal_actions"] == ["EXIT_BREAK", "CHECK_OUT"]
    assert body["decision_token"]
    broke = await confirm(client, headers, body["decision_token"], "EXIT_BREAK")
    assert broke.json()["state"] == "ON_BREAK"
    assert broke.json()["breaks_used"] == 1

    # 3. RE_ENTRY — offered alongside CHECK_OUT, so it still asks.
    third = await scan(client, headers, token, gate_id=gate_id)
    assert set(third.json()["legal_actions"]) == {"RE_ENTRY", "CHECK_OUT"}
    back = await confirm(client, headers, third.json()["decision_token"], "RE_ENTRY")
    assert back.json()["state"] == "INSIDE"

    # 4. CHECK_OUT — the break is spent, so it is the only thing on the card.
    fourth = await scan(client, headers, token, gate_id=gate_id)
    assert fourth.json()["legal_actions"] == ["CHECK_OUT"]
    out = await confirm(client, headers, fourth.json()["decision_token"], "CHECK_OUT")
    assert out.json()["state"] == "DEPARTED"

    # 5. DEPARTED is terminal.
    after = await scan(client, headers, token, gate_id=gate_id)
    assert after.status_code == 409
    assert after.json()["error"]["code"] == "INVALID_STATE"

    final = await db_session.get(Pass, issued_pass.id)
    await db_session.refresh(final)
    assert final.current_state is PassState.DEPARTED
    assert final.breaks_used == 1
    assert final.version == 4
    assert final.first_checkin_at and final.last_exit_at and final.checked_out_at

    ok_scans = await db_session.scalar(
        select(func.count()).select_from(Scan).where(
            Scan.pass_id == issued_pass.id, Scan.result == ScanResult.OK.value
        )
    )
    assert ok_scans == 4


async def test_a_pass_with_no_break_is_scanned_twice(
    client, db_session, scanner_staff, issued_pass, token, no_duplicate_window
):
    headers = await login(client, SECURITY_NET_ID)
    await check_in(client, headers, token)
    second = await scan(client, headers, token)
    out = await confirm(client, headers, second.json()["decision_token"], "CHECK_OUT")

    assert out.json()["state"] == "DEPARTED"
    assert out.json()["breaks_used"] == 0


# ── duplicate suppression (§11.2) ────────────────────────────────────────


async def test_a_double_tap_returns_the_previous_result_not_an_error(
    client, db_session, scanner_staff, issued_pass, token
):
    """Operators double-tap constantly; an error screen makes them rescan."""
    headers = await login(client, SECURITY_NET_ID)
    first = await check_in(client, headers, token)
    second = await scan(client, headers, token)

    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["action"] == first.json()["action"] == "CHECK_IN"
    assert second.json()["state"] == "INSIDE"

    transitions = await db_session.scalar(
        select(func.count()).select_from(Scan).where(
            Scan.pass_id == issued_pass.id, Scan.result == ScanResult.OK.value
        )
    )
    suppressed = await db_session.scalar(
        select(func.count()).select_from(Scan).where(
            Scan.pass_id == issued_pass.id, Scan.result == ScanResult.DUPLICATE.value
        )
    )
    assert (transitions, suppressed) == (1, 1)


async def test_replaying_one_client_scan_uuid_cannot_double_count(
    client, db_session, scanner_staff, issued_pass, token, no_duplicate_window
):
    """The offline queue replays; `UNIQUE (pass_id, client_scan_uuid)` is what
    stops a replayed CHECK_IN counting twice even with the window off."""
    headers = await login(client, SECURITY_NET_ID)
    scan_uuid = uuid4()
    first = await check_in(client, headers, token, client_scan_uuid=scan_uuid)
    replay = await scan(client, headers, token, client_scan_uuid=scan_uuid)

    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["action"] == first.json()["action"]

    rows = await db_session.scalar(
        select(func.count()).select_from(Scan).where(Scan.pass_id == issued_pass.id)
    )
    assert rows == 1


# ── token and pass guards ────────────────────────────────────────────────


async def test_a_forged_signature_is_refused_and_recorded(
    client, db_session, scanner_staff, event_id, token
):
    headers = await login(client, SECURITY_NET_ID)
    prefix, payload, signature = token.split(".")
    forged = f"{prefix}.{payload}.{'A' * len(signature)}"

    response = await scan(client, headers, forged)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"

    denial = (
        await db_session.execute(
            select(Scan).where(Scan.event_id == event_id, Scan.pass_id.is_(None))
        )
    ).scalar_one()
    assert denial.result == ScanResult.DENIED.value
    assert denial.deny_reason == "INVALID_SIGNATURE"
    # The token is a bearer credential: only its fingerprint is ever stored.
    assert denial.raw_token_hash and forged not in denial.raw_token_hash


async def test_an_unknown_pass_is_a_404(client, scanner_staff, event_id):
    headers = await login(client, SECURITY_NET_ID)
    orphan = mint_pass_token(
        event_id=event_id,
        pass_id=uuid4(),
        serial=1,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    response = await scan(client, headers, orphan)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_PASS"


async def test_a_superseded_serial_is_refused(
    client, db_session, scanner_staff, issued_pass, token
):
    """A reissued pass dies by serial, without its pass_id changing (§10.1)."""
    headers = await login(client, SECURITY_NET_ID)
    # Mutated through the ORM, not raw SQL: the app shares this session in
    # tests, so a raw UPDATE would leave the identity map holding the old row
    # and the test would pass against a pass that never changed.
    issued_pass.serial += 1
    await db_session.commit()

    response = await scan(client, headers, token)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PASS_SUPERSEDED"


async def test_refreshing_a_departed_pass_makes_it_scannable_again(
    client, db_session, scanner_staff, issued_pass, token
):
    """The rehearsal case. Check-out is binding, so a pass walked all the way
    through is finished — which is correct on the night and useless when the
    same pass is the only thing available to test a camera with.

    `refresh_in_place` winds it back AND bumps the serial, so the old QR dies
    in the same breath as the new one is born. Both halves are asserted: a
    refresh that left the old token working would quietly hand somebody a
    second, parallel way through the door.
    """
    from app.services.passes.issue import refresh_in_place

    headers = await login(client, SECURITY_NET_ID)

    issued_pass.current_state = PassState.DEPARTED
    issued_pass.breaks_used = 1
    issued_pass.first_checkin_at = datetime.now(UTC) - timedelta(hours=2)
    issued_pass.checked_out_at = datetime.now(UTC)
    await db_session.commit()

    # A departed pass has nothing left to offer.
    refused = await scan(client, headers, token)
    assert refused.status_code == 409, refused.text

    stale_token = token
    refresh_in_place(issued_pass)
    # Snapshot before the commit: committing expires every ORM attribute, and
    # reading one back afterwards is lazy IO in a sync context (MissingGreenlet).
    event_id, pass_id, serial = issued_pass.event_id, issued_pass.id, issued_pass.serial
    await db_session.commit()

    # The QR that was on the screen a moment ago is dead.
    superseded = await scan(client, headers, stale_token)
    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == "PASS_SUPERSEDED"

    # And the pass itself is a clean slate: a fresh token checks in normally.
    fresh = mint_pass_token(
        event_id=event_id,
        pass_id=pass_id,
        serial=serial,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    presented = await scan(client, headers, fresh)
    assert presented.status_code == 200, presented.text
    assert presented.json()["legal_actions"] == ["CHECK_IN"]

    refreshed = await db_session.get(Pass, pass_id)
    assert refreshed.breaks_used == 0
    assert refreshed.checked_out_at is None


async def test_a_revoked_pass_denies_every_scan(
    client, db_session, scanner_staff, issued_pass, token
):
    headers = await login(client, SECURITY_NET_ID)
    issued_pass.current_state = PassState.REVOKED
    issued_pass.revoked_at = datetime.now(UTC)
    await db_session.commit()

    response = await scan(client, headers, token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASS_REVOKED"


async def test_an_expired_pass_row_is_refused_even_with_a_live_token(
    client, db_session, scanner_staff, issued_pass, event_id
):
    """Token expiry and pass expiry are separate facts; both are checked."""
    headers = await login(client, SECURITY_NET_ID)
    live_token = mint_pass_token(
        event_id=event_id,
        pass_id=issued_pass.id,
        serial=issued_pass.serial,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    issued_pass.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.commit()

    response = await scan(client, headers, live_token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PASS_EXPIRED"


async def test_a_gate_from_another_event_is_refused(client, scanner_staff, issued_pass, token):
    headers = await login(client, SECURITY_NET_ID)
    response = await scan(client, headers, token, gate_id=uuid4())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "GATE_NOT_PERMITTED"


# ── decision tokens ──────────────────────────────────────────────────────


async def test_a_decision_token_is_single_use(
    client, scanner_staff, issued_pass, token, no_duplicate_window
):
    headers = await login(client, SECURITY_NET_ID)
    await check_in(client, headers, token)
    decision = (await scan(client, headers, token)).json()["decision_token"]

    assert (await confirm(client, headers, decision, "EXIT_BREAK")).status_code == 200
    replay = await confirm(client, headers, decision, "CHECK_OUT")
    # Replayed with a DIFFERENT action: it must not apply, and the operator
    # gets back what the token actually did rather than an error screen.
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["state"] == "ON_BREAK"


async def test_an_unknown_decision_token_is_refused(client, scanner_staff, issued_pass):
    headers = await login(client, SECURITY_NET_ID)
    response = await confirm(client, headers, "not-a-real-decision-token", "CHECK_OUT")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DECISION_TOKEN_EXPIRED"


async def test_a_decision_belongs_to_the_operator_who_scanned(
    client, scanner_staff, issued_pass, token, no_duplicate_window
):
    """Otherwise a second phone could spend a decision another gate is still
    looking at."""
    headers = await login(client, SECURITY_NET_ID)
    await scan(client, headers, token)
    decision = (await scan(client, headers, token)).json()["decision_token"]

    other = await login(client, "other.guard")
    response = await confirm(client, other, decision, "CHECK_OUT")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DECISION_TOKEN_INVALID"


async def test_confirm_refuses_an_action_that_was_not_offered(
    client, scanner_staff, issued_pass, token, no_duplicate_window
):
    headers = await login(client, SECURITY_NET_ID)
    await check_in(client, headers, token)
    decision = (await scan(client, headers, token)).json()["decision_token"]

    response = await confirm(client, headers, decision, "CHECK_IN")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"


# ── §11.5 response shape ─────────────────────────────────────────────────

SECURITY_FIELDS = {
    "status", "duplicate", "participant_name", "id_image_url", "state",
    "breaks_used", "max_breaks", "legal_actions", "decision_token",
    "decision_expires_in", "action",
}


async def test_a_security_response_carries_only_the_11_5_fields(
    client, scanner_staff, issued_pass, token
):
    """The narrow card is the whole channel through which a volunteer sees
    PII. No NetID, no pass id, no ticket reference, no history."""
    headers = await login(client, SECURITY_NET_ID)
    body = (await scan(client, headers, token)).json()

    assert set(body) == SECURITY_FIELDS
    assert body["participant_name"] == "Aarav Sharma"
    assert "net_id" not in body
    assert "pass_id" not in body
    assert "short_code" not in body


async def test_an_owner_gets_the_full_record(client, scanner_staff, issued_pass, token):
    headers = await login(client, OWNER_NET_ID)
    body = (await check_in(client, headers, token)).json()

    assert SECURITY_FIELDS < set(body)
    assert body["pass_id"] == str(issued_pass.id)
    assert body["short_code"] == issued_pass.short_code
    assert body["serial"] == issued_pass.serial
    assert body["scan_id"]


async def test_the_scan_row_records_who_scanned_and_where(
    client, db_session, scanner_staff, gate_id, issued_pass, token
):
    headers = await login(client, SECURITY_NET_ID)
    await check_in(client, headers, token, gate_id=gate_id)

    row = (
        await db_session.execute(select(Scan).where(Scan.pass_id == issued_pass.id))
    ).scalar_one()
    assert row.gate_id == gate_id
    assert row.scanned_by is not None
    assert row.action is ScanAction.CHECK_IN
    assert row.from_state is PassState.NOT_ARRIVED
    assert row.to_state is PassState.INSIDE
