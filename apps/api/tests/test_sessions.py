from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis


@pytest_asyncio.fixture
async def redis(settings):
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_create_then_load_round_trips(redis):
    from app.services.identity.sessions import create_session, load_session

    user_id = uuid4()
    created = await create_session(
        redis, user_id=user_id, net_id="owner.test", display_name="Owner Test",
        email="owner@university.edu", roles=["OWNER"], is_staff=True,
    )
    loaded = await load_session(redis, created.sid)

    assert loaded is not None
    assert loaded.user_id == user_id
    assert loaded.roles == ["OWNER"]
    assert loaded.csrf_token == created.csrf_token


async def test_unknown_sid_loads_as_none(redis):
    from app.services.identity.sessions import load_session

    assert await load_session(redis, "not-a-real-sid") is None


async def test_staff_ttl_is_fixed_and_participant_ttl_slides(redis, settings):
    from app.services.identity.sessions import create_session, load_session

    staff = await create_session(
        redis, user_id=uuid4(), net_id="admin.test", display_name="A",
        email=None, roles=["ADMIN"], is_staff=True,
    )
    visitor = await create_session(
        redis, user_id=uuid4(), net_id="visitor.test", display_name="V",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    assert await redis.ttl(f"sess:{staff.sid}") == pytest.approx(
        settings.session_ttl_staff_seconds, abs=5
    )

    await redis.expire(f"sess:{visitor.sid}", 60)
    await load_session(redis, visitor.sid)  # sliding refresh
    assert await redis.ttl(f"sess:{visitor.sid}") == pytest.approx(
        settings.session_ttl_participant_seconds, abs=5
    )

    await redis.expire(f"sess:{staff.sid}", 60)
    await load_session(redis, staff.sid)    # fixed — must NOT extend
    assert await redis.ttl(f"sess:{staff.sid}") <= 60


async def test_destroy_session_removes_it(redis):
    from app.services.identity.sessions import create_session, destroy_session, load_session

    session = await create_session(
        redis, user_id=uuid4(), net_id="x", display_name="X",
        email=None, roles=["VISITOR"], is_staff=False,
    )
    await destroy_session(redis, session.sid)
    assert await load_session(redis, session.sid) is None


async def test_destroy_user_sessions_kills_every_device(redis):
    """LLD-v2 §0: 'you see a device list and can kill any session'."""
    from app.services.identity.sessions import (
        create_session,
        destroy_user_sessions,
        load_session,
    )

    user_id = uuid4()
    sessions = [
        await create_session(
            redis, user_id=user_id, net_id="sec.test", display_name="S",
            email=None, roles=["SECURITY"], is_staff=True,
        )
        for _ in range(3)
    ]
    other = await create_session(
        redis, user_id=uuid4(), net_id="other", display_name="O",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    killed = await destroy_user_sessions(redis, user_id)

    assert killed == 3
    for session in sessions:
        assert await load_session(redis, session.sid) is None
    assert await load_session(redis, other.sid) is not None


async def test_user_index_is_pruned_on_single_destroy(redis):
    from app.services.identity.sessions import create_session, destroy_session

    user_id = uuid4()
    session = await create_session(
        redis, user_id=user_id, net_id="x", display_name="X",
        email=None, roles=["VISITOR"], is_staff=False,
    )
    await destroy_session(redis, session.sid)
    assert await redis.smembers(f"sess:user:{user_id}") == set()


async def test_shorter_second_session_does_not_shrink_the_user_index_ttl(redis, settings):
    """Pins the fix for F-A: Redis EXPIRE *replaces* a TTL, it does not extend
    it. If the per-user index's TTL were derived from whichever session is
    being created right now, a staff member (12h TTL) who is later resolved
    as a plain VISITOR (8h TTL) on a second sign-in -- exactly what happens
    when their `event_staff` row is deactivated mid-event and they log back
    in -- would shrink `sess:user:<id>`'s expiry down from 12h+ to 8h+, and
    the still-live 12h staff session would fall out of the index while it is
    still live. `destroy_user_sessions` walks the index, so it would silently
    find nothing and report 0 kills for a session that is very much still
    valid: exactly the "stolen scanner phone" / "staff removed mid-event"
    scenario CLAUDE.md names as the whole reason these sessions are opaque.
    """
    from app.services.identity.sessions import (
        create_session,
        destroy_user_sessions,
        load_session,
    )

    user_id = uuid4()
    staff = await create_session(
        redis, user_id=user_id, net_id="staff.test", display_name="S",
        email=None, roles=["SECURITY"], is_staff=True,
    )
    # Second sign-in, same identity, now resolved with a shorter TTL.
    participant = await create_session(
        redis, user_id=user_id, net_id="staff.test", display_name="S",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    index_ttl = await redis.ttl(f"sess:user:{user_id}")
    assert index_ttl >= settings.session_ttl_staff_seconds

    killed = await destroy_user_sessions(redis, user_id)
    assert killed == 2
    assert await load_session(redis, staff.sid) is None
    assert await load_session(redis, participant.sid) is None


# ── CSRF ─────────────────────────────────────────────────────────────────
#
# `require_csrf` must be double-submit in the real sense: the token has to
# arrive somewhere a cross-site request cannot put it automatically (a custom
# header, or a form field the page itself set) — never compared against the
# CSRF cookie, which rides along with every cross-site request exactly like
# the session cookie does. A cookie-vs-cookie comparison only proves the
# browser is logged in, which is true of the attacker's forged request too.


def _build_csrf_app() -> FastAPI:
    """A tiny throwaway app exercising require_csrf. Never add test routes to
    the real app.main.app — this app exists only for this test module.
    """
    from app.core.deps import get_redis, require_csrf
    from app.core.errors import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)

    @app.post("/protected", dependencies=[Depends(require_csrf)])
    async def protected_post():
        return {"ok": True}

    @app.get("/protected", dependencies=[Depends(require_csrf)])
    async def protected_get():
        return {"ok": True}

    # Placeholder; overridden per-test with the fixture's real redis client.
    app.dependency_overrides[get_redis] = lambda: None
    return app


@pytest_asyncio.fixture
async def csrf_client(redis, settings):
    """A (client, session) pair authenticated with a real session in Redis,
    wired to the real require_csrf dependency.
    """
    from app.core.deps import get_redis
    from app.services.identity.sessions import create_session

    session = await create_session(
        redis, user_id=uuid4(), net_id="csrf.test", display_name="C",
        email=None, roles=["VISITOR"], is_staff=False,
    )

    app = _build_csrf_app()
    app.dependency_overrides[get_redis] = lambda: redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.session_cookie_name, session.sid)
        yield client, session


async def test_csrf_header_token_passes(csrf_client):
    client, session = csrf_client
    response = await client.post("/protected", headers={"x-csrf-token": session.csrf_token})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_csrf_form_field_token_passes(csrf_client):
    client, session = csrf_client
    response = await client.post("/protected", data={"csrf_token": session.csrf_token})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_csrf_cookie_only_is_rejected(csrf_client, settings):
    """Pins the fix: presenting the CSRF value only as a cookie (no header, no
    form field) must be rejected. This is exactly what a forged cross-site
    request looks like — the browser attaches cookies automatically.
    """
    client, session = csrf_client
    client.cookies.set(settings.csrf_cookie_name, session.csrf_token)
    response = await client.post("/protected")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


async def test_csrf_wrong_or_missing_token_is_rejected(csrf_client):
    client, _session = csrf_client
    wrong = await client.post("/protected", headers={"x-csrf-token": "wrong-token"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "CSRF_INVALID"

    missing = await client.post("/protected")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_INVALID"


async def test_csrf_malformed_multipart_body_is_rejected_with_envelope(csrf_client):
    """Pins the fix for F-B: a body that fails to parse (here, a
    multipart/form-data content-type with no boundary, so python-multipart's
    parser raises) must fall through to the normal 403 CSRF_INVALID path in
    the project's own error envelope -- not escape as Starlette's built-in
    HTTPException (400, `{"detail": ...}`) which bypasses our error handlers.
    """
    client, _session = csrf_client
    response = await client.post(
        "/protected",
        content=b"not a valid multipart body",
        headers={"content-type": "multipart/form-data"},  # missing boundary
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "CSRF_INVALID"
    assert "detail" not in body


async def test_csrf_get_with_no_token_passes(csrf_client):
    client, _session = csrf_client
    response = await client.get("/protected")
    assert response.status_code == 200
