import pytest
from sqlalchemy import select, text


@pytest.fixture
async def seeded_staff(db_session, event_id):
    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role) VALUES "
            "(gen_random_uuid(), :e, 'owner.test', 'OWNER'), "
            "(gen_random_uuid(), :e, 'admin.test', 'ADMIN'), "
            "(gen_random_uuid(), :e, 'security.test', 'SECURITY')"
        ),
        {"e": event_id},
    )
    await db_session.flush()


async def _dev_login(client, net_id: str):
    start = await client.get("/auth/start", params={"next": "/"}, follow_redirects=False)
    assert start.status_code == 307
    state = start.headers["location"].split("state=")[1].split("&")[0]
    return await client.post(
        "/auth/callback",
        data={"state": state, "net_id": net_id},
        follow_redirects=False,
    )


async def test_login_page_renders(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "Sign in" in response.text


async def test_dev_login_as_owner_sets_a_session_with_owner_role(client, seeded_staff, settings):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    response = await _dev_login(client, "owner.test")

    assert response.status_code == 303
    sid = response.cookies[settings.session_cookie_name]
    session = await load_session(get_redis_client(), sid)
    assert session is not None
    # Staff carry VISITOR alongside their staff role, so an OWNER can also buy a
    # ticket. is_staff is derived from STAFF_ROLE_NAMES, not from the list being
    # exactly one entry, so the extra member must not change the session TTL.
    assert session.roles == ["OWNER", "VISITOR"]
    assert session.is_staff is True
    assert session.net_id == "owner.test"


@pytest.mark.parametrize(
    ("net_id", "expected"),
    [
        ("owner.test", ["OWNER", "VISITOR"]),
        ("admin.test", ["ADMIN", "VISITOR"]),
        ("security.test", ["SECURITY", "VISITOR"]),
        ("random.student", ["VISITOR"]),
    ],
)
async def test_roles_resolve_per_net_id(client, seeded_staff, settings, net_id, expected):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    response = await _dev_login(client, net_id)
    session = await load_session(get_redis_client(), response.cookies[settings.session_cookie_name])
    assert session.roles == expected


async def test_unseeded_net_id_is_a_visitor_not_an_error(client, seeded_staff):
    response = await _dev_login(client, "random.student")
    assert response.status_code == 303


async def test_login_creates_exactly_one_user_row_across_two_logins(
    client, db_session, seeded_staff
):
    from sqlalchemy import func

    from app.models import User

    await _dev_login(client, "owner.test")
    await _dev_login(client, "owner.test")

    count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.net_id == "owner.test")
    )
    assert count == 1


async def test_login_backfills_event_staff_user_id(client, db_session, seeded_staff):
    from app.models import EventStaff, User

    await _dev_login(client, "owner.test")

    user = (await db_session.execute(select(User).where(User.net_id == "owner.test"))).scalar_one()
    staff = (
        await db_session.execute(select(EventStaff).where(EventStaff.net_id == "owner.test"))
    ).scalar_one()
    assert staff.user_id == user.id


async def test_callback_with_an_unknown_state_is_rejected(client, seeded_staff):
    response = await client.post(
        "/auth/callback", data={"state": "forged", "net_id": "owner.test"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OIDC_STATE_INVALID"


async def test_state_is_single_use(client, seeded_staff):
    start = await client.get("/auth/start", params={"next": "/"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]

    first = await client.post(
        "/auth/callback", data={"state": state, "net_id": "owner.test"}, follow_redirects=False
    )
    second = await client.post(
        "/auth/callback", data={"state": state, "net_id": "owner.test"}, follow_redirects=False
    )

    assert first.status_code == 303
    assert second.status_code == 400


async def test_dev_picker_is_404_when_provider_is_entra(monkeypatch, db_session):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_db
    from app.core.settings import get_settings
    from app.main import create_app

    monkeypatch.setenv("AUTH_PROVIDER", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "c")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s")
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/auth/dev/picker", params={"state": "x"})
    assert response.status_code == 404


async def test_logout_destroys_the_session(client, seeded_staff, settings):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    login = await _dev_login(client, "owner.test")
    sid = login.cookies[settings.session_cookie_name]

    session = await load_session(get_redis_client(), sid)
    response = await client.post(
        "/auth/logout",
        headers={"x-csrf-token": session.csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert await load_session(get_redis_client(), sid) is None


@pytest.mark.parametrize(
    "malicious_next",
    ["//evil.example", "/\\evil.example", "https://evil.example"],
)
async def test_open_redirect_next_values_fall_back_to_the_default(client, event_id, malicious_next):
    start = await client.get(
        "/auth/start", params={"next": malicious_next}, follow_redirects=False
    )
    assert start.status_code == 307
    state = start.headers["location"].split("state=")[1].split("&")[0]

    response = await client.post(
        "/auth/callback",
        data={"state": state, "net_id": "owner.test"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/me"


async def test_ordinary_next_value_is_honoured(client, event_id):
    start = await client.get(
        "/auth/start", params={"next": "/api/v1/me"}, follow_redirects=False
    )
    state = start.headers["location"].split("state=")[1].split("&")[0]

    response = await client.post(
        "/auth/callback",
        data={"state": state, "net_id": "owner.test"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/api/v1/me"


async def test_deactivated_staff_row_does_not_grant_a_role(client, db_session, event_id, settings):
    from app.core.deps import get_redis_client
    from app.services.identity.sessions import load_session

    await db_session.execute(
        text(
            "INSERT INTO event_staff (id, event_id, net_id, role, is_active) VALUES "
            "(gen_random_uuid(), :e, 'inactive.owner', 'OWNER', false), "
            "(gen_random_uuid(), :e, 'active.owner', 'OWNER', true)"
        ),
        {"e": event_id},
    )
    await db_session.flush()

    inactive = await _dev_login(client, "inactive.owner")
    inactive_session = await load_session(
        get_redis_client(), inactive.cookies[settings.session_cookie_name]
    )
    # Deactivated: VISITOR only. The staff role is genuinely gone, not merely
    # accompanied by VISITOR -- that distinction is the point of this test.
    assert inactive_session.roles == ["VISITOR"]
    assert "OWNER" not in inactive_session.roles

    active = await _dev_login(client, "active.owner")
    active_session = await load_session(
        get_redis_client(), active.cookies[settings.session_cookie_name]
    )
    assert active_session.roles == ["OWNER", "VISITOR"]
