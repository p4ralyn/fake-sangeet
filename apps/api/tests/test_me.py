import pytest
from sqlalchemy import func, select, text


@pytest.fixture
async def seeded_staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, 'owner.test', 'OWNER'), "
            "(gen_random_uuid(), :e, 'admin.test', 'ADMIN')"
        ),
        {"e": event_id},
    )
    await db_session.flush()


async def _dev_login(client, net_id: str):
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    return await client.post(
        "/auth/callback", data={"state": state, "net_id": net_id}, follow_redirects=False
    )


async def test_me_requires_a_session(client):
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_me_returns_the_owner_payload(client, seeded_staff):
    await _dev_login(client, "owner.test")
    response = await client.get("/api/v1/me")

    assert response.status_code == 200
    body = response.json()
    assert body["net_id"] == "owner.test"
    assert body["roles"] == ["OWNER"]
    assert body["display_name"] == "Owner Test"
    assert body["event"]["slug"] == "dryrun"
    assert body["csrf_token"]
    assert set(body) == {
        "user_id", "net_id", "display_name", "email", "roles", "event", "csrf_token"
    }


async def test_me_returns_visitor_for_an_unseeded_net_id(client, seeded_staff):
    await _dev_login(client, "random.student")
    response = await client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["roles"] == ["VISITOR"]


async def test_require_role_denies_a_visitor(client, seeded_staff):
    await _dev_login(client, "random.student")
    response = await client.get("/api/v1/me/owner-only")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_require_role_allows_the_owner(client, seeded_staff):
    await _dev_login(client, "owner.test")
    response = await client.get("/api/v1/me/owner-only")

    assert response.status_code == 200


async def test_require_role_denies_an_admin_from_an_owner_route(client, seeded_staff):
    await _dev_login(client, "admin.test")
    assert (await client.get("/api/v1/me/owner-only")).status_code == 403


async def test_login_writes_an_audit_row_and_a_funnel_row(client, db_session, seeded_staff):
    from app.models import AuditLog, FunnelEvent

    await _dev_login(client, "owner.test")

    audit = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "LOGIN")
    )
    funnel = await db_session.scalar(
        select(func.count()).select_from(FunnelEvent).where(FunnelEvent.step == "LOGIN")
    )
    assert audit == 1
    assert funnel == 1


async def test_audit_row_stores_no_raw_email(client, db_session, seeded_staff):
    from app.models import AuditLog, User

    await _dev_login(client, "owner.test")
    user = (
        await db_session.execute(select(User).where(User.net_id == "owner.test"))
    ).scalar_one()
    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "LOGIN"))
    row = result.scalar_one()

    assert user.email  # sanity: the dev provider actually issued one
    assert row.ip_hash is None or "@" not in row.ip_hash
    assert row.after is None or user.email not in str(row.after)
    assert row.after is None or "owner.test" not in str(row.after)


async def test_require_role_denies_an_unrecognised_role_string_instead_of_500ing(
    client, settings, event_id
):
    """A session can outlive a deploy (staff sessions live 12h). If a `Role`
    member is ever renamed or removed, a still-live session's cookie can carry
    a string `Role(...)` no longer accepts. That must deny cleanly, not 500 --
    constructed directly via `create_session`, since the login flow can only
    ever mint valid roles."""
    from uuid import uuid4

    from app.core.deps import get_redis_client
    from app.services.identity.sessions import create_session

    session = await create_session(
        get_redis_client(),
        user_id=uuid4(),
        net_id="odd.staff",
        display_name="Odd Staff",
        email=None,
        roles=["RETIRED_ROLE"],
        is_staff=True,
    )
    client.cookies.set(settings.session_cookie_name, session.sid)

    owner_only = await client.get("/api/v1/me/owner-only")
    assert owner_only.status_code == 403
    assert owner_only.json()["error"]["code"] == "FORBIDDEN"

    me_response = await client.get("/api/v1/me")
    assert me_response.status_code == 200
    assert me_response.json()["roles"] == []
