"""Scanning — one transaction per scan. LLD-v1 §11.2, §11.3.

    BEGIN;
      SELECT * FROM passes WHERE id = $1 FOR UPDATE;   -- serialises two gates
      -- validate current_state against the requested action
      INSERT INTO scans (...);                          -- append-only
      UPDATE passes SET current_state=..., version=version+1 WHERE id=$1 AND version=$3;
    COMMIT;

The row lock is the whole correctness story: two gates scanning the same QR at
the same instant queue behind it, and the second one sees the state the first
one left. The `version` guard on the UPDATE is belt-and-braces — it cannot fail
while the lock is held, and it fails loudly if the lock is ever removed.

This module owns its transaction boundary. Routes do not commit scans: the
transaction *is* the invariant, so it does not get to be a caller's decision.
"""
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import structlog
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.models import Event, Gate, Order, Pass, PassState, Scan, ScanAction, ScanResult, Ticket
from app.models.user import User
from app.models.verification import IdImage, Registration, Verification, VerifyOutcome
from app.services.access.decisions import (
    DecisionRecord,
    consume_decision,
    issue_decision,
    recall_outcome,
    remember_outcome,
)
from app.services.access.state_machine import (
    DEFAULT_MAX_BREAKS,
    IllegalTransition,
    Transition,
    apply,
    auto_action,
    legal_actions,
)
from app.services.audit import write_audit
from app.services.passes.token import PassTokenError, token_fingerprint, verify_pass_token
from app.services.storage.provider import get_object_store

log = structlog.get_logger()

# §13.6 codes carry a fixed HTTP status so the scanner's red/amber screens are
# decided by the code, not by a status the route happened to pick.
_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_TOKEN: 400,
    ErrorCode.INVALID_SIGNATURE: 403,
    ErrorCode.TOKEN_WRONG_EVENT: 403,
    ErrorCode.UNKNOWN_PASS: 404,
    ErrorCode.PASS_EXPIRED: 403,
    ErrorCode.PASS_REVOKED: 403,
    ErrorCode.PASS_SUPERSEDED: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.GATE_NOT_PERMITTED: 403,
    ErrorCode.DECISION_TOKEN_INVALID: 403,
    ErrorCode.DECISION_TOKEN_EXPIRED: 400,
}


class AccessConfig(BaseModel):
    """`events.config.access`. Pydantic at the boundary, including this one."""

    max_breaks: int = DEFAULT_MAX_BREAKS


class ScanContext(BaseModel):
    """What the scanner reported about this attempt."""

    gate_id: UUID | None = None
    device_id: UUID | None = None
    client_scan_uuid: UUID
    occurred_at: datetime
    offline: bool = False
    actor_user_id: UUID | None = None


class ScanOutcome(BaseModel):
    """Role-agnostic result. The route projects it onto the response model the
    caller's role is allowed to see — never by filtering fields at runtime."""

    status: Literal["DONE", "ACTION_REQUIRED"]
    duplicate: bool = False
    pass_id: UUID
    short_code: str
    serial: int
    version: int
    state: PassState
    breaks_used: int
    max_breaks: int
    participant_name: str | None = None
    id_image_url: str | None = None
    legal_actions: list[ScanAction] = []
    decision_token: str | None = None
    decision_expires_in: int | None = None
    action: ScanAction | None = None
    from_state: PassState | None = None
    scan_id: UUID | None = None


def _error(code: ErrorCode, message: str) -> AppError:
    return AppError(code, message, http_status=_HTTP_STATUS.get(code, 400))


async def _approved_id_image(db: AsyncSession, ticket_id: UUID) -> IdImage | None:
    """The ID photo that was actually approved for this ticket.

    Only an APPROVED verification counts. An image still sitting in the review
    queue is not evidence of anything, and showing it at the door would put a
    volunteer in the position of doing the reviewer's job at 9pm with a queue
    behind them.
    """
    return await db.scalar(
        select(IdImage)
        .select_from(Verification)
        .join(Registration, Registration.id == Verification.registration_id)
        .join(IdImage, IdImage.id == Verification.id_image_id)
        .where(
            Registration.ticket_id == ticket_id,
            Registration.superseded_at.is_(None),
            Verification.outcome == VerifyOutcome.APPROVED,
            IdImage.purged_at.is_(None),
        )
        .order_by(Verification.attempt_no.desc())
        .limit(1)
    )


def access_config(event: Event) -> AccessConfig:
    return AccessConfig.model_validate((event.config or {}).get("access") or {})


async def _participant_name(db: AsyncSession, ticket_id: UUID) -> str | None:
    """The name on the card the volunteer is eyeballing.

    Prefers `registrations.full_name` — what the person themselves gave when
    they uploaded their ID, and therefore the name most likely to match the
    card in the volunteer's hand. Falls back to the signed-in display name,
    then to the purchaser for a PROXY ticket, which by definition has nobody
    signed in behind it.
    """
    registered = await db.scalar(
        select(Registration.full_name).where(
            Registration.ticket_id == ticket_id,
            # A superseded registration belongs to whoever held this ticket
            # before it was transferred. Showing their name at the door would
            # point a volunteer at the wrong face.
            Registration.superseded_at.is_(None),
        )
    )
    if registered:
        return registered
    row = (
        await db.execute(
            select(User.display_name, Order.purchaser_name)
            .select_from(Ticket)
            .join(Order, Order.id == Ticket.order_id)
            .outerjoin(User, User.id == Ticket.user_id)
            .where(Ticket.id == ticket_id)
        )
    ).first()
    if row is None:
        return None
    return row[0] or row[1]


async def _validate_gate(db: AsyncSession, event_id: UUID, gate_id: UUID | None) -> None:
    if gate_id is None:
        return
    gate = await db.scalar(
        select(Gate).where(Gate.id == gate_id, Gate.event_id == event_id, Gate.is_active.is_(True))
    )
    if gate is None:
        raise _error(ErrorCode.GATE_NOT_PERMITTED, "That gate is not open for this event.")


async def _record_denial(
    db: AsyncSession,
    *,
    event_id: UUID,
    pass_: Pass | None,
    context: ScanContext,
    reason: str,
    token_hash: str | None,
    action: ScanAction = ScanAction.DENIED,
) -> None:
    """A refusal is forensics, not noise — a forged QR is exactly the row you
    want at 11 p.m. Committed before the error is raised, because raising
    rolls nothing back that we still want."""
    db.add(
        Scan(
            event_id=event_id,
            pass_id=pass_.id if pass_ else None,
            gate_id=context.gate_id,
            scanned_by=context.actor_user_id,
            device_id=context.device_id,
            action=action,
            from_state=pass_.current_state if pass_ else None,
            to_state=None,
            result=ScanResult.DENIED.value,
            deny_reason=reason,
            offline=context.offline,
            client_scan_uuid=context.client_scan_uuid,
            occurred_at=context.occurred_at,
            raw_token_hash=token_hash,
        )
    )
    await db.commit()


async def _locked_pass(db: AsyncSession, event_id: UUID, pass_id: UUID) -> Pass | None:
    """The line that serialises two gates."""
    return await db.scalar(
        select(Pass).where(Pass.id == pass_id, Pass.event_id == event_id).with_for_update()
    )


async def _replayed_scan(db: AsyncSession, pass_id: UUID, client_scan_uuid: UUID) -> Scan | None:
    """`UNIQUE (pass_id, client_scan_uuid)` in query form: an offline device
    replaying its queue must not double-count."""
    return await db.scalar(
        select(Scan).where(Scan.pass_id == pass_id, Scan.client_scan_uuid == client_scan_uuid)
    )


async def _recent_success(
    db: AsyncSession, pass_id: UUID, *, action: ScanAction | None, now: datetime
) -> Scan | None:
    """§11.2 anti-passback. Operators double-tap constantly; returning the
    previous result beats an error screen that makes them rescan and panic."""
    window = get_settings().scan_duplicate_window_seconds
    if window <= 0:
        return None
    stmt = select(Scan).where(
        Scan.pass_id == pass_id,
        Scan.result == ScanResult.OK.value,
        Scan.recorded_at >= now - timedelta(seconds=window),
    )
    if action is not None:
        stmt = stmt.where(Scan.action == action)
    return await db.scalar(stmt.order_by(Scan.recorded_at.desc()).limit(1))


async def _outcome_from_scan(
    db: AsyncSession, pass_: Pass, scan: Scan, *, max_breaks: int, duplicate: bool
) -> ScanOutcome:
    """Re-serve a scan we already recorded. A replayed *denial* is re-raised as
    the same refusal rather than dressed up as a success — an offline device
    replaying a rejected scan must not come back green."""
    if scan.result == ScanResult.DENIED.value:
        try:
            code = ErrorCode(scan.deny_reason)
        except ValueError:
            code = ErrorCode.INVALID_STATE
        raise _error(code, "This scan was already refused.")
    return ScanOutcome(
        status="DONE",
        duplicate=duplicate,
        pass_id=pass_.id,
        short_code=pass_.short_code,
        serial=pass_.serial,
        version=pass_.version,
        state=pass_.current_state,
        breaks_used=pass_.breaks_used,
        max_breaks=max_breaks,
        participant_name=await _participant_name(db, pass_.ticket_id),
        legal_actions=list(
            legal_actions(
                pass_.current_state, breaks_used=pass_.breaks_used, max_breaks=max_breaks
            )
        ),
        action=scan.action,
        from_state=scan.from_state,
        scan_id=scan.id,
    )


async def _record_duplicate(
    db: AsyncSession, *, pass_: Pass, previous: Scan, context: ScanContext, token_hash: str | None
) -> None:
    """The suppressed attempt still gets a row — how often operators double-tap
    is worth knowing, and `scans` is the only place it can be counted."""
    db.add(
        Scan(
            event_id=pass_.event_id,
            pass_id=pass_.id,
            gate_id=context.gate_id,
            scanned_by=context.actor_user_id,
            device_id=context.device_id,
            action=previous.action,
            from_state=pass_.current_state,
            to_state=pass_.current_state,
            result=ScanResult.DUPLICATE.value,
            offline=context.offline,
            client_scan_uuid=context.client_scan_uuid,
            occurred_at=context.occurred_at,
            raw_token_hash=token_hash,
        )
    )
    await db.commit()


async def _commit_transition(
    db: AsyncSession,
    *,
    pass_: Pass,
    transition: Transition,
    context: ScanContext,
    token_hash: str | None,
    now: datetime,
) -> Scan:
    """INSERT the scan, then the guarded UPDATE. One transaction, in that order."""
    scan = Scan(
        event_id=pass_.event_id,
        pass_id=pass_.id,
        gate_id=context.gate_id,
        scanned_by=context.actor_user_id,
        device_id=context.device_id,
        action=transition.action,
        from_state=transition.from_state,
        to_state=transition.to_state,
        result=ScanResult.OK.value,
        offline=context.offline,
        client_scan_uuid=context.client_scan_uuid,
        occurred_at=context.occurred_at,
        raw_token_hash=token_hash,
    )
    db.add(scan)
    await db.flush()

    values: dict = {
        "current_state": transition.to_state,
        "breaks_used": transition.breaks_used,
        "last_scan_at": now,
        "version": pass_.version + 1,
    }
    if transition.action is ScanAction.CHECK_IN:
        values["first_checkin_at"] = pass_.first_checkin_at or now
    if transition.action is ScanAction.EXIT_BREAK:
        values["last_exit_at"] = now
    if transition.action is ScanAction.CHECK_OUT:
        values["checked_out_at"] = now

    result = await db.execute(
        update(Pass)
        .where(Pass.id == pass_.id, Pass.version == pass_.version)
        .values(**values)
    )
    if result.rowcount != 1:
        # Unreachable while the FOR UPDATE above is held. If it ever fires, the
        # lock is gone and admitting anyone on this result would be a guess.
        await db.rollback()
        raise _error(ErrorCode.INVALID_STATE, "This pass changed mid-scan. Scan again.")

    await db.commit()
    log.info(
        "scan_applied",
        pass_id=str(pass_.id),
        action=transition.action.value,
        from_state=transition.from_state.value,
        to_state=transition.to_state.value,
    )
    return scan


def _applied_outcome(
    pass_: Pass, transition: Transition, scan: Scan, *, max_breaks: int, name: str | None
) -> ScanOutcome:
    return ScanOutcome(
        status="DONE",
        pass_id=pass_.id,
        short_code=pass_.short_code,
        serial=pass_.serial,
        version=pass_.version + 1,
        state=transition.to_state,
        breaks_used=transition.breaks_used,
        max_breaks=max_breaks,
        participant_name=name,
        legal_actions=list(
            legal_actions(
                transition.to_state, breaks_used=transition.breaks_used, max_breaks=max_breaks
            )
        ),
        action=transition.action,
        from_state=transition.from_state,
        scan_id=scan.id,
    )


async def _guard_pass(
    db: AsyncSession,
    *,
    pass_: Pass,
    expected_serial: int,
    context: ScanContext,
    token_hash: str | None,
    now: datetime,
) -> None:
    """§11.2 guards that need the locked row. Order matters: the most specific
    reason wins, because the reason is what the volunteer acts on."""
    if pass_.serial != expected_serial:
        await _record_denial(
            db, event_id=pass_.event_id, pass_=pass_, context=context,
            reason=ErrorCode.PASS_SUPERSEDED.value, token_hash=token_hash,
        )
        raise _error(
            ErrorCode.PASS_SUPERSEDED, "This pass was reissued. Ask for the newest QR."
        )
    if pass_.current_state is PassState.REVOKED:
        await _record_denial(
            db, event_id=pass_.event_id, pass_=pass_, context=context,
            reason=ErrorCode.PASS_REVOKED.value, token_hash=token_hash,
        )
        raise _error(ErrorCode.PASS_REVOKED, "This pass has been revoked. Do not admit.")
    if pass_.expires_at <= now:
        await _record_denial(
            db, event_id=pass_.event_id, pass_=pass_, context=context,
            reason=ErrorCode.PASS_EXPIRED.value, token_hash=token_hash,
        )
        raise _error(ErrorCode.PASS_EXPIRED, "This pass has expired.")


async def present_token(
    db: AsyncSession,
    redis: Redis,
    *,
    event: Event,
    token: str,
    context: ScanContext,
) -> ScanOutcome:
    """`POST /api/v1/scans`. Either a completed transition or ACTION_REQUIRED."""
    now = datetime.now(UTC)
    max_breaks = access_config(event).max_breaks
    token_hash = token_fingerprint(token)

    try:
        claims = verify_pass_token(token, expected_event_id=event.id, now=now)
    except PassTokenError as exc:
        await _record_denial(
            db, event_id=event.id, pass_=None, context=context,
            reason=exc.code.value, token_hash=token_hash,
        )
        raise _error(exc.code, exc.message) from exc

    await _validate_gate(db, event.id, context.gate_id)

    pass_ = await _locked_pass(db, event.id, claims.pass_id)
    if pass_ is None:
        await _record_denial(
            db, event_id=event.id, pass_=None, context=context,
            reason=ErrorCode.UNKNOWN_PASS.value, token_hash=token_hash,
        )
        raise _error(ErrorCode.UNKNOWN_PASS, "No such pass. Send them to the desk.")

    replay = await _replayed_scan(db, pass_.id, context.client_scan_uuid)
    if replay is not None:
        return await _outcome_from_scan(db, pass_, replay, max_breaks=max_breaks, duplicate=True)

    await _guard_pass(
        db, pass_=pass_, expected_serial=claims.serial, context=context,
        token_hash=token_hash, now=now,
    )

    previous = await _recent_success(db, pass_.id, action=None, now=now)
    if previous is not None:
        await _record_duplicate(
            db, pass_=pass_, previous=previous, context=context, token_hash=token_hash
        )
        return await _outcome_from_scan(db, pass_, previous, max_breaks=max_breaks, duplicate=True)

    actions = legal_actions(
        pass_.current_state, breaks_used=pass_.breaks_used, max_breaks=max_breaks
    )
    if not actions:
        await _record_denial(
            db, event_id=event.id, pass_=pass_, context=context,
            reason=ErrorCode.INVALID_STATE.value, token_hash=token_hash,
        )
        raise _error(
            ErrorCode.INVALID_STATE,
            f"Nothing is allowed from {pass_.current_state.value}.",
        )

    name = await _participant_name(db, pass_.ticket_id)
    chosen = auto_action(actions)
    if chosen is None:
        decision_token, ttl = await issue_decision(
            redis,
            DecisionRecord(
                event_id=event.id,
                pass_id=pass_.id,
                serial=pass_.serial,
                legal_actions=list(actions),
                gate_id=context.gate_id,
                device_id=context.device_id,
                client_scan_uuid=context.client_scan_uuid,
                occurred_at=context.occurred_at,
                offline=context.offline,
                scanned_by=context.actor_user_id,
                token_hash=token_hash,
            ),
        )
        # Read the ID row while the transaction is still open, but do NOT sign
        # yet: signing is CPU on a threadpool and the row lock is still held.
        id_image = await _approved_id_image(db, pass_.ticket_id)
        image_id = id_image.id if id_image else None
        image_key = id_image.object_key if id_image else None

        outcome = ScanOutcome(
            status="ACTION_REQUIRED",
            pass_id=pass_.id,
            short_code=pass_.short_code,
            serial=pass_.serial,
            version=pass_.version,
            state=pass_.current_state,
            breaks_used=pass_.breaks_used,
            max_breaks=max_breaks,
            participant_name=name,
            legal_actions=list(actions),
            decision_token=decision_token,
            decision_expires_in=ttl,
        )
        # Snapshot every id needed after the rollback below. A rollback expires
        # EVERY loaded ORM object -- `event` as much as `pass_` -- and reading
        # an expired attribute from here would attempt lazy IO that asyncpg
        # cannot service.
        scanned_pass_id = pass_.id
        scanned_event_id = event.id
        # Nothing was written, so release the row lock rather than hold it
        # across the operator's think time. Built the response first: rollback
        # expires every loaded object, and `pass_` is one of them.
        await db.rollback()

        if image_key is not None:
            outcome.id_image_url = await get_object_store().signed_url(
                image_key, expires_seconds=get_settings().id_image_url_ttl_seconds
            )
            # LLD-v1 §9.2: this is the narrow channel through which SECURITY
            # ever sees PII, and every opening of it is logged with the
            # scanner's identity. Written after the rollback above, which would
            # otherwise discard it, and committed on its own.
            await write_audit(
                db,
                event_id=scanned_event_id,
                actor_user_id=context.actor_user_id,
                actor_net_id=None,
                actor_role=None,
                action="ID_IMAGE_VIEWED",
                object_type="id_image",
                object_id=image_id,
                # Both raw: `write_audit` coerces. They used to disagree — one
                # str()-ed, one not — and that asymmetry is exactly what hid a
                # UUID that JSONB could not store.
                after={"pass_id": scanned_pass_id, "device_id": context.device_id},
            )
            await db.commit()
        return outcome

    transition = apply(
        pass_.current_state, chosen, breaks_used=pass_.breaks_used, max_breaks=max_breaks
    )
    scan = await _commit_transition(
        db, pass_=pass_, transition=transition, context=context, token_hash=token_hash, now=now
    )
    return _applied_outcome(pass_, transition, scan, max_breaks=max_breaks, name=name)


async def confirm_decision(
    db: AsyncSession,
    redis: Redis,
    *,
    event: Event,
    decision_token: str,
    action: ScanAction,
    actor_user_id: UUID | None,
) -> ScanOutcome:
    """`POST /api/v1/scans/confirm`. The operator's choice, applied under the
    same lock as an auto transition."""
    now = datetime.now(UTC)
    max_breaks = access_config(event).max_breaks

    record = await consume_decision(redis, decision_token)
    if record is None:
        replayed = await recall_outcome(redis, decision_token)
        if replayed is not None:
            scan = await db.get(Scan, replayed)
            pass_ = await db.get(Pass, scan.pass_id) if scan else None
            if scan is not None and pass_ is not None:
                return await _outcome_from_scan(
                    db, pass_, scan, max_breaks=max_breaks, duplicate=True
                )
        raise _error(
            ErrorCode.DECISION_TOKEN_EXPIRED,
            "That decision expired or was already used. Scan again.",
        )

    if record.event_id != event.id or (
        record.scanned_by is not None and record.scanned_by != actor_user_id
    ):
        raise _error(ErrorCode.DECISION_TOKEN_INVALID, "That decision is not yours.")
    if action not in record.legal_actions:
        raise _error(
            ErrorCode.INVALID_STATE, f"{action.value} was not offered for this scan."
        )

    context = ScanContext(
        gate_id=record.gate_id,
        device_id=record.device_id,
        client_scan_uuid=record.client_scan_uuid,
        occurred_at=record.occurred_at,
        offline=record.offline,
        actor_user_id=actor_user_id,
    )

    pass_ = await _locked_pass(db, event.id, record.pass_id)
    if pass_ is None:
        raise _error(ErrorCode.UNKNOWN_PASS, "No such pass. Send them to the desk.")

    replay = await _replayed_scan(db, pass_.id, context.client_scan_uuid)
    if replay is not None:
        return await _outcome_from_scan(db, pass_, replay, max_breaks=max_breaks, duplicate=True)

    await _guard_pass(
        db, pass_=pass_, expected_serial=record.serial, context=context,
        token_hash=record.token_hash, now=now,
    )

    previous = await _recent_success(db, pass_.id, action=action, now=now)
    if previous is not None:
        await _record_duplicate(
            db, pass_=pass_, previous=previous, context=context, token_hash=record.token_hash
        )
        return await _outcome_from_scan(db, pass_, previous, max_breaks=max_breaks, duplicate=True)

    try:
        transition = apply(
            pass_.current_state, action, breaks_used=pass_.breaks_used, max_breaks=max_breaks
        )
    except IllegalTransition as exc:
        await _record_denial(
            db, event_id=event.id, pass_=pass_, context=context,
            reason=exc.reason.value, token_hash=record.token_hash,
        )
        code = (
            ErrorCode.PASS_REVOKED
            if exc.reason.value == ErrorCode.PASS_REVOKED.value
            else ErrorCode.INVALID_STATE
        )
        raise _error(code, exc.message) from exc

    name = await _participant_name(db, pass_.ticket_id)
    scan = await _commit_transition(
        db, pass_=pass_, transition=transition, context=context,
        token_hash=record.token_hash, now=now,
    )
    await remember_outcome(redis, decision_token, scan.id)
    return _applied_outcome(pass_, transition, scan, max_breaks=max_breaks, name=name)
