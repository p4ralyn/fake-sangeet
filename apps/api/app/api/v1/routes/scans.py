from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis, require_csrf, require_role
from app.schemas.scans import (
    ScanConfirmRequest,
    ScanRequest,
    SecurityScanResponse,
    StaffScanResponse,
)
from app.services.access.scans import ScanContext, ScanOutcome, confirm_decision, present_token
from app.services.identity.roles import SCAN_FULL_VIEW_ROLES, Role, get_current_event, parse_roles
from app.services.identity.sessions import SessionData

router = APIRouter(prefix="/api/v1", tags=["scans"])

scanner = require_role(Role.SECURITY, Role.ADMIN, Role.OWNER)


def _project(outcome: ScanOutcome, session: SessionData) -> SecurityScanResponse:
    """Pick the response model for the caller's role.

    Deliberately not `response_model=` on the route: a union response model
    would make FastAPI re-validate at serialisation time, and a SECURITY
    response that happened to satisfy the richer model would be emitted in
    full. Choosing the class here means the narrow model is the only shape a
    volunteer's phone can ever receive.
    """
    if set(parse_roles(session.roles)) & SCAN_FULL_VIEW_ROLES:
        return StaffScanResponse.from_outcome(outcome)
    return SecurityScanResponse.from_outcome(outcome)


@router.post("/scans", response_model=None, dependencies=[Depends(require_csrf)])
async def present_scan(
    payload: ScanRequest,
    session: SessionData = Depends(scanner),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SecurityScanResponse:
    event = await get_current_event(db)
    outcome = await present_token(
        db,
        redis,
        event=event,
        token=payload.token,
        context=ScanContext(
            gate_id=payload.gate_id,
            device_id=payload.device_id,
            client_scan_uuid=payload.client_scan_uuid,
            occurred_at=payload.occurred_at,
            offline=payload.offline_replay,
            actor_user_id=session.user_id,
        ),
    )
    return _project(outcome, session)


@router.post("/scans/confirm", response_model=None, dependencies=[Depends(require_csrf)])
async def confirm_scan(
    payload: ScanConfirmRequest,
    session: SessionData = Depends(scanner),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> SecurityScanResponse:
    event = await get_current_event(db)
    outcome = await confirm_decision(
        db,
        redis,
        event=event,
        decision_token=payload.decision_token,
        action=payload.action,
        actor_user_id=session.user_id,
    )
    return _project(outcome, session)
