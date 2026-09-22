"""The M4 endpoints over HTTP, and the loop they close with the scanner.

The service tests cover what a decision *means*. These cover who is allowed to
ask, what the wire shapes look like, and — the part that matters most — that an
approved ID actually reaches a volunteer's phone at the gate as a short-lived
signed URL, which is the only channel through which SECURITY ever sees PII
(LLD-v1 §9.2, §11.5).
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select, text

from app.models import AuditLog, Event, Order, Ticket, TicketStatus
from tests.images import jpeg_bytes

HOLDER_NET_ID = "pk944"
ADMIN_NET_ID = "zq202"
SECURITY_NET_ID = "zq203"


async def login(client, net_id: str) -> dict[str, str]:
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )
    return {"x-csrf-token": client.cookies.get("ug_csrf")}


async def logout(client) -> None:
    client.cookies.clear()


def upload_body(raw: bytes | None = None, *, consent: str = "true"):
    return {
        "files": {"image": ("id.jpg", raw if raw is not None else jpeg_bytes(), "image/jpeg")},
        "data": {"consent": consent},
    }


@pytest_asyncio.fixture
async def staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, :admin, 'ADMIN'), "
            "(gen_random_uuid(), :e, :sec, 'SECURITY')"
        ),
        {"e": event_id, "admin": ADMIN_NET_ID, "sec": SECURITY_NET_ID},
    )
    await db_session.commit()


@pytest_asyncio.fixture
async def ticket(db_session, event_id) -> Ticket:
    order = Order(
        event_id=event_id,
        external_id="route-row-1",
        purchaser_email=f"{HOLDER_NET_ID}@snu.edu.in",
        purchaser_name="Aarav Sharma",
        quantity=1,
    )
    db_session.add(order)
    await db_session.flush()
    row = Ticket(
        event_id=event_id,
        order_id=order.id,
        ticket_ref="FS26-ROUTE-1",
        status=TicketStatus.PURCHASED,
        bound_net_id=HOLDER_NET_ID,
    )
    db_session.add(row)
    await db_session.commit()
    return row


# ── authorisation ────────────────────────────────────────────────────────────
async def test_uploading_requires_a_session(client):
    response = await client.post("/api/v1/me/id-image", **upload_body())
    assert response.status_code == 401


async def test_uploading_without_a_csrf_token_is_refused(client, ticket):
    await login(client, HOLDER_NET_ID)
    response = await client.post("/api/v1/me/id-image", **upload_body())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_the_review_queue_is_closed_to_a_participant(client, ticket):
    headers = await login(client, HOLDER_NET_ID)
    response = await client.get("/api/v1/admin/verifications", headers=headers)
    assert response.status_code == 403


async def test_the_review_queue_is_closed_to_security(client, staff, ticket):
    """SECURITY works the door, not the queue. A gate volunteer holding a
    borrowed phone must not be able to page through 600 student IDs."""
    headers = await login(client, SECURITY_NET_ID)
    response = await client.get("/api/v1/admin/verifications", headers=headers)
    assert response.status_code == 403


async def test_somebody_with_no_ticket_gets_a_404_not_a_500(client, event_id):
    headers = await login(client, "zz999")
    assert (await client.get("/api/v1/me/verification", headers=headers)).status_code == 404
    response = await client.post("/api/v1/me/id-image", headers=headers, **upload_body())
    assert response.status_code == 404


# ── the participant's side ───────────────────────────────────────────────────
async def test_the_status_endpoint_says_upload_is_open_before_anything_happens(
    client, ticket
):
    headers = await login(client, HOLDER_NET_ID)
    body = (await client.get("/api/v1/me/verification", headers=headers)).json()

    assert body["can_upload"] is True
    assert body["blocked_reason"] is None
    assert body["ticket_status"] == "PURCHASED"
    assert body["attempts_used"] == 0
    assert body["attempts_remaining"] >= 1


async def test_an_upload_returns_the_new_status_and_closes_the_button(client, ticket):
    headers = await login(client, HOLDER_NET_ID)

    created = await client.post("/api/v1/me/id-image", headers=headers, **upload_body())

    assert created.status_code == 201
    body = created.json()
    assert body["ticket_status"] == "MANUAL_REVIEW"
    assert body["outcome"] == "MANUAL_REVIEW"
    assert body["attempts_used"] == 1
    assert body["can_upload"] is False
    assert "waiting to be checked" in body["blocked_reason"]


async def test_an_upload_without_consent_is_a_409_with_a_readable_reason(client, ticket):
    headers = await login(client, HOLDER_NET_ID)
    response = await client.post(
        "/api/v1/me/id-image", headers=headers, **upload_body(consent="false")
    )
    assert response.status_code == 409
    assert "consent" in response.json()["error"]["message"]


async def test_a_file_that_is_not_an_image_is_a_422_with_a_readable_reason(client, ticket):
    headers = await login(client, HOLDER_NET_ID)
    response = await client.post(
        "/api/v1/me/id-image", headers=headers, **upload_body(b"<html>nope</html>")
    )
    assert response.status_code == 422
    assert "JPEG or PNG" in response.json()["error"]["message"]


# ── the reviewer's side ──────────────────────────────────────────────────────
async def test_an_admin_sees_the_queue_with_a_working_image_link(client, staff, ticket):
    participant = await login(client, HOLDER_NET_ID)
    await client.post("/api/v1/me/id-image", headers=participant, **upload_body())
    await logout(client)

    headers = await login(client, ADMIN_NET_ID)
    body = (await client.get("/api/v1/admin/verifications", headers=headers)).json()

    assert body["pending"] == 1
    item = body["items"][0]
    assert item["net_id"] == HOLDER_NET_ID
    assert item["attempt_no"] == 1
    assert item["id_image_url"]

    # The link actually resolves to the stored bytes.
    from app.core.http import get_http_client

    fetched = await get_http_client().get(item["id_image_url"], timeout=10)
    assert fetched.status_code == 200
    assert fetched.content.startswith(b"\xff\xd8")


async def test_approving_over_http_issues_the_pass(client, db_session, staff, ticket):
    participant = await login(client, HOLDER_NET_ID)
    await client.post("/api/v1/me/id-image", headers=participant, **upload_body())
    await logout(client)

    headers = await login(client, ADMIN_NET_ID)
    queue = (await client.get("/api/v1/admin/verifications", headers=headers)).json()
    target = queue["items"][0]["verification_id"]

    decided = await client.post(
        f"/api/v1/admin/verifications/{target}/decide",
        headers=headers,
        json={"approve": True},
    )

    assert decided.status_code == 200
    assert decided.json()["outcome"] == "APPROVED"
    assert decided.json()["ticket_status"] == "PASS_ISSUED"

    from app.models import Pass

    issued = await db_session.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    assert issued is not None


async def test_deciding_the_same_review_twice_is_a_409(client, staff, ticket):
    participant = await login(client, HOLDER_NET_ID)
    await client.post("/api/v1/me/id-image", headers=participant, **upload_body())
    await logout(client)

    headers = await login(client, ADMIN_NET_ID)
    queue = (await client.get("/api/v1/admin/verifications", headers=headers)).json()
    target = queue["items"][0]["verification_id"]
    body = {"approve": False, "note": "Blurry", "allow_resubmit": True}

    assert (
        await client.post(
            f"/api/v1/admin/verifications/{target}/decide", headers=headers, json=body
        )
    ).status_code == 200
    second = await client.post(
        f"/api/v1/admin/verifications/{target}/decide", headers=headers, json=body
    )
    assert second.status_code == 409


async def test_deciding_a_review_that_does_not_exist_is_a_404(client, staff, ticket):
    headers = await login(client, ADMIN_NET_ID)
    response = await client.post(
        f"/api/v1/admin/verifications/{uuid4()}/decide",
        headers=headers,
        json={"approve": True},
    )
    assert response.status_code == 404


# ── the loop the scanner has been waiting on ─────────────────────────────────
async def test_an_approved_id_reaches_the_gate_as_a_signed_url_and_is_audited(
    client, db_session, event_id, staff, ticket
):
    """The whole point of M4 from the door's side: the volunteer sees the face
    that was verified, through a 90-second link, and the fact that they looked
    is on the record."""
    participant = await login(client, HOLDER_NET_ID)
    await client.post("/api/v1/me/id-image", headers=participant, **upload_body())
    await logout(client)

    admin = await login(client, ADMIN_NET_ID)
    queue = (await client.get("/api/v1/admin/verifications", headers=admin)).json()
    target = queue["items"][0]["verification_id"]
    await client.post(
        f"/api/v1/admin/verifications/{target}/decide", headers=admin, json={"approve": True}
    )
    await logout(client)

    # Walk the pass to a state that needs an operator decision, so the scan
    # comes back ACTION_REQUIRED and therefore carries the image.
    from app.models import Pass, PassState
    from app.services.passes.token import mint_pass_token

    issued = await db_session.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    # Through the ORM, not raw SQL: the scan resolves the same identity-mapped
    # object, and a raw UPDATE would leave it holding a stale state.
    issued.current_state = PassState.INSIDE
    await db_session.commit()
    assert issued.current_state is PassState.INSIDE

    token = mint_pass_token(
        event_id=event_id,
        pass_id=issued.id,
        serial=issued.serial,
        expires_at=issued.expires_at,
    )

    guard = await login(client, SECURITY_NET_ID)
    scanned = await client.post(
        "/api/v1/scans",
        headers=guard,
        json={
            "token": token,
            "client_scan_uuid": str(uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )

    assert scanned.status_code == 200
    body = scanned.json()
    assert body["status"] == "ACTION_REQUIRED"
    assert body["id_image_url"], "an approved ID must reach the gate"
    # The dev provider's display name for pk944. Against real Google sign-in
    # this is the person's own name; either way it comes from
    # `registrations.full_name`, which is the name they verified under.
    assert body["participant_name"] == "Pk944"

    viewed = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ID_IMAGE_VIEWED")
    )
    assert viewed is not None, "§9.2: every opening of that channel is logged"


async def test_an_unreviewed_id_does_not_reach_the_gate(
    client, db_session, event_id, staff, ticket
):
    """A photo still sitting in the queue is not evidence of anything. Showing
    it at the door would make the volunteer the reviewer."""
    from app.models import PassState
    from app.services.passes.issue import issue_pass
    from app.services.passes.token import mint_pass_token

    participant = await login(client, HOLDER_NET_ID)
    await client.post("/api/v1/me/id-image", headers=participant, **upload_body())
    await logout(client)

    # A pass exists, but nobody has approved the ID behind it.
    event = await db_session.get(Event, event_id)
    fresh = await db_session.get(Ticket, ticket.id)
    issued = await issue_pass(db_session, event=event, ticket=fresh)
    issued.current_state = PassState.INSIDE
    await db_session.commit()

    token = mint_pass_token(
        event_id=event_id,
        pass_id=issued.id,
        serial=issued.serial,
        expires_at=issued.expires_at,
    )
    guard = await login(client, SECURITY_NET_ID)
    body = (
        await client.post(
            "/api/v1/scans",
            headers=guard,
            json={
                "token": token,
                "client_scan_uuid": str(uuid4()),
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
    ).json()

    assert body["status"] == "ACTION_REQUIRED"
    assert body["id_image_url"] is None


async def test_a_security_scan_response_still_carries_no_extra_fields(
    client, db_session, event_id, staff, ticket
):
    """M4 adds a field to the rich model. The narrow one must not grow with it
    — that is why they are separate classes and not one filtered dict."""
    from app.services.passes.issue import issue_pass
    from app.services.passes.token import mint_pass_token

    event = await db_session.get(Event, event_id)
    fresh = await db_session.get(Ticket, ticket.id)
    issued = await issue_pass(db_session, event=event, ticket=fresh)
    await db_session.commit()
    token = mint_pass_token(
        event_id=event_id,
        pass_id=issued.id,
        serial=issued.serial,
        expires_at=issued.expires_at,
    )

    guard = await login(client, SECURITY_NET_ID)
    body = (
        await client.post(
            "/api/v1/scans",
            headers=guard,
            json={
                "token": token,
                "client_scan_uuid": str(uuid4()),
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
    ).json()

    assert "pass_id" not in body
    assert "short_code" not in body
    assert "registration_id" not in body
