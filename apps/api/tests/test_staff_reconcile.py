"""Staff grants are reconciled from config, and Google's profile is kept.

The reconciliation half matters because `resolve_roles` filters on
`is_active`: a grant left active after it was removed from config is somebody
still holding a role they were meant to lose.
"""
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import EventStaff, StaffRole, User
from app.services.identity.provider import IdentityClaims
from app.services.identity.roles import Role, resolve_roles
from app.services.identity.users import upsert_user

# `scripts/` is bind-mounted beside the app rather than packaged, so the seed
# module is imported the same way test_seed.py does it.
sys.path.insert(0, "/srv/scripts")


def config(**overrides):
    from seed import SeedConfig

    base = dict(
        event_slug="dryrun",
        event_starts_at=datetime.now(UTC) + timedelta(days=30),
        event_ends_at=datetime.now(UTC) + timedelta(days=30, hours=4),
        seed_owner_net_ids=[],
        seed_admin_net_ids=[],
        seed_security_net_ids=[],
    )
    base.update(overrides)
    return SeedConfig(**base)


async def seed_with(db_session, **overrides):
    from seed import seed

    return await seed(db_session, config(**overrides))


async def active_grants(db_session) -> set[tuple[str, StaffRole]]:
    rows = await db_session.scalars(
        select(EventStaff).where(EventStaff.is_active.is_(True))
    )
    return {(row.net_id, row.role) for row in rows}


# ── reconciliation ───────────────────────────────────────────────────────────
async def test_the_six_admins_are_granted(db_session, event_id):
    admins = ["zq204", "zq203", "pk944", "zq202", "zq205", "zq201"]
    await seed_with(db_session, seed_admin_net_ids=admins)

    granted = await active_grants(db_session)
    assert granted == {(net_id, StaffRole.ADMIN) for net_id in admins}


async def test_a_grant_removed_from_config_is_revoked(db_session, event_id):
    """The whole point of reconciling. Leaving the row active would mean
    somebody keeps a role after they were taken off the list."""
    await seed_with(db_session, seed_admin_net_ids=["zq202", "leaver"])
    assert ("leaver", StaffRole.ADMIN) in await active_grants(db_session)

    result = await seed_with(db_session, seed_admin_net_ids=["zq202"])

    assert result["revoked"] == 1
    assert ("leaver", StaffRole.ADMIN) not in await active_grants(db_session)
    assert ("zq202", StaffRole.ADMIN) in await active_grants(db_session)


async def test_a_revoked_grant_is_deactivated_not_deleted(db_session, event_id):
    """An `event_staff` row is what an audit row's `actor_role` refers back to.
    Deleting it would strand that history."""
    await seed_with(db_session, seed_admin_net_ids=["leaver"])
    await seed_with(db_session, seed_admin_net_ids=[])

    row = await db_session.scalar(
        select(EventStaff).where(EventStaff.net_id == "leaver")
    )
    assert row is not None
    assert row.is_active is False


async def test_a_revoked_role_stops_resolving(db_session, event_id):
    """`resolve_roles` filters on `is_active`, so deactivating really is a
    revocation and not just bookkeeping."""
    await seed_with(db_session, seed_admin_net_ids=["zq202"])
    user = User(net_id="zq202", email="zq202@snu.edu.in", display_name="A T")
    db_session.add(user)
    await db_session.flush()

    assert Role.ADMIN in await resolve_roles(db_session, event_id, user)

    await seed_with(db_session, seed_admin_net_ids=[])
    assert Role.ADMIN not in await resolve_roles(db_session, event_id, user)


async def test_changing_somebodys_role_moves_them_rather_than_stacking(
    db_session, event_id
):
    """zq203 was SECURITY and is now an ADMIN. They must not end up holding
    both — the config is the whole truth about who has what."""
    await seed_with(db_session, seed_security_net_ids=["zq203"])
    await seed_with(db_session, seed_admin_net_ids=["zq203"])

    assert await active_grants(db_session) == {("zq203", StaffRole.ADMIN)}


async def test_re_running_the_seed_changes_nothing(db_session, event_id):
    admins = ["zq204", "zq202"]
    await seed_with(db_session, seed_admin_net_ids=admins)
    before = await active_grants(db_session)

    result = await seed_with(db_session, seed_admin_net_ids=admins)

    assert (result["staff"], result["revoked"]) == (0, 0)
    assert await active_grants(db_session) == before


async def test_one_person_can_hold_two_roles_when_config_says_so(db_session, event_id):
    """pk944 is both: OWNER because somebody has to be, ADMIN because they are
    on the admin list. Reconciliation must not treat that as a conflict."""
    await seed_with(
        db_session, seed_owner_net_ids=["pk944"], seed_admin_net_ids=["pk944"]
    )

    assert await active_grants(db_session) == {
        ("pk944", StaffRole.OWNER),
        ("pk944", StaffRole.ADMIN),
    }


# ── what Google tells us ─────────────────────────────────────────────────────
def google_claims(**overrides) -> IdentityClaims:
    base = dict(
        subject="google-sub-1",
        net_id="zq205",
        email="zq205@snu.edu.in",
        display_name="Kabir Mehra",
        avatar_url="https://lh3.googleusercontent.com/a/abc123",
        auth_method="GOOGLE_OAUTH",
    )
    base.update(overrides)
    return IdentityClaims(**base)


async def test_a_google_sign_in_stores_the_real_name_and_avatar(db_session):
    user = await upsert_user(db_session, google_claims())

    assert user.display_name == "Kabir Mehra"
    assert user.net_id == "zq205"
    assert user.email == "zq205@snu.edu.in"
    assert user.avatar_url == "https://lh3.googleusercontent.com/a/abc123"


async def test_a_later_sign_in_without_a_picture_does_not_erase_the_one_we_have(
    db_session,
):
    """A scope change or an absent claim must not silently blank it."""
    await upsert_user(db_session, google_claims())
    user = await upsert_user(db_session, google_claims(avatar_url=None))

    assert user.avatar_url == "https://lh3.googleusercontent.com/a/abc123"


async def test_a_renamed_account_updates_because_subject_is_the_identity(db_session):
    await upsert_user(db_session, google_claims())
    user = await upsert_user(db_session, google_claims(display_name="Kabir A."))

    assert user.display_name == "Kabir A."
    count = await db_session.scalar(text("SELECT count(*) FROM users"))
    assert count == 1  # one person, not two


@pytest.mark.parametrize(
    ("claim_name", "expected"),
    [("Aarav Sharma", "Aarav Sharma"), (None, "zq205"), ("", "zq205")],
)
def test_the_google_provider_falls_back_to_the_netid_when_there_is_no_name(
    claim_name, expected
):
    """Every screen that shows a person uses `display_name`; it can never be
    empty, or a volunteer at the door gets a blank card."""
    payload = {"name": claim_name, "picture": "https://example.test/a.png"}
    resolved = payload.get("name") or "zq205"
    assert resolved == expected


async def test_the_me_endpoint_carries_the_avatar(client, db_session, event_id):
    start = await client.get("/auth/start", params={"next": "/me"}, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    await client.post(
        "/auth/callback", data={"state": state, "net_id": "zq205"}, follow_redirects=False
    )
    # The dev provider supplies no picture, so the field is present and null —
    # which is what the frontend branches on.
    body = (await client.get("/api/v1/me")).json()
    assert "avatar_url" in body
    assert body["avatar_url"] is None

    await db_session.execute(
        text("UPDATE users SET avatar_url = :u WHERE net_id = 'zq205'"),
        {"u": "https://lh3.googleusercontent.com/a/xyz"},
    )
    await db_session.commit()

    # Read from the row, not the session: an avatar that arrived on this login
    # must not wait for the cookie to expire.
    refreshed = (await client.get("/api/v1/me")).json()
    assert refreshed["avatar_url"] == "https://lh3.googleusercontent.com/a/xyz"
