"""ID upload and the review queue (M4).

Thin: validate, authorize, call a service, shape a response. Every rule about
who may upload what, and what a decision does to a ticket, lives in
`services/verification/service.py`.

The API takes the image bytes rather than issuing a signed resumable upload URL
(LLD-v1 §9.2). That deviation is deliberate and local to this deployment: §9.2's
concern was 600 concurrent uploads melting Cloud Run, and this runs on one VM
behind Caddy with uploads spread over days, not seconds. Proxying the bytes buys
the thing that matters more here — the server sees every file before it is
stored, so EXIF stripping and format sniffing are not advice to a client, they
are what happened. It also keeps `docker compose up` a complete environment,
since fake-gcs cannot sign anything.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, require_csrf, require_role
from app.core.errors import AppError, ErrorCode
from app.models import User
from app.schemas.verification import (
    MyVerification,
    ReviewDecision,
    ReviewItem,
    ReviewQueue,
)
from app.services.identity.roles import (
    STAFF_ROLE_NAMES,
    Role,
    get_current_event,
    parse_roles,
)
from app.services.identity.sessions import SessionData
from app.services.storage.provider import ObjectStore, get_object_store
from app.services.verification import service
from app.services.verification.images import MAX_UPLOAD_BYTES, UnsupportedImage

router = APIRouter(prefix="/api/v1", tags=["verification"])

reviewer = require_role(Role.OWNER, Role.ADMIN)

#: The queue page size a caller may ask for. A reviewer works a screen at a
#: time; an unbounded limit would mint a signed URL for every ID at once.
MAX_QUEUE_LIMIT = 200


def get_store() -> ObjectStore:
    return get_object_store()


def _not_found(message: str) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, message, http_status=status.HTTP_404_NOT_FOUND)


async def _user(db: AsyncSession, session: SessionData) -> User:
    found = await db.get(User, session.user_id)
    if found is None:  # pragma: no cover -- a live session implies a live user
        raise AppError(
            ErrorCode.UNAUTHENTICATED,
            "Sign in to continue.",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    return found


def _actor_role(session: SessionData) -> str | None:
    """The `staff_role` enum value or NULL — never "PARTICIPANT", which is not a
    member of that PG enum. Same derivation `record_login` uses."""
    return next(
        (role.value for role in parse_roles(session.roles) if role.value in STAFF_ROLE_NAMES),
        None,
    )


@router.get("/me/verification", response_model=MyVerification)
async def my_verification(
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MyVerification:
    """This person's own ID check. Drives the upload screen — the button's
    enabled state is `can_upload`, never the frontend's reading of a status."""
    event = await get_current_event(db)
    user = await _user(db, session)
    try:
        return await service.my_verification_view(db, event=event, user=user)
    except service.NoTicket:
        raise _not_found("We can’t find a ticket for your account.") from None


@router.post(
    "/me/id-image",
    response_model=MyVerification,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def upload_id_image(
    image: UploadFile = File(...),
    consent: bool = Form(False),
    phone: str | None = Form(None),
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_store),
) -> MyVerification:
    """Take one ID photo and open a review.

    There is no registration id in this request. Which ticket, which
    registration and which attempt this belongs to are derived from the
    session, so there is nothing here to tamper with.
    """
    event = await get_current_event(db)
    user = await _user(db, session)

    # Read with a ceiling rather than trusting `UploadFile.size`, which is the
    # client's word for it. One byte past the limit is enough to know.
    raw = await image.read(MAX_UPLOAD_BYTES + 1)

    try:
        await service.submit_id_image(
            db, store, event=event, user=user, raw=raw, consented=consent, phone=phone
        )
    except service.NoTicket:
        raise _not_found("We can’t find a ticket for your account.") from None
    except service.UploadNotAllowed as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR, str(exc), http_status=status.HTTP_409_CONFLICT
        ) from exc
    except UnsupportedImage as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc

    view = await service.my_verification_view(db, event=event, user=user)
    await db.commit()
    return view


@router.get("/admin/verifications", response_model=ReviewQueue)
async def review_queue(
    limit: int = 50,
    offset: int = 0,
    session: SessionData = Depends(reviewer),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_store),
) -> ReviewQueue:
    """The queue, oldest first — somebody who uploaded an hour ago has been
    waiting an hour."""
    event = await get_current_event(db)
    return await service.review_queue_view(
        db,
        store,
        event_id=event.id,
        limit=min(limit, MAX_QUEUE_LIMIT),
        offset=max(offset, 0),
    )


@router.post(
    "/admin/verifications/{verification_id}/decide",
    response_model=ReviewItem,
    dependencies=[Depends(require_csrf)],
)
async def decide(
    verification_id: UUID,
    decision: ReviewDecision,
    session: SessionData = Depends(reviewer),
    db: AsyncSession = Depends(get_db),
    store: ObjectStore = Depends(get_store),
) -> ReviewItem:
    """Approve or refuse one ID. Approving issues the pass."""
    event = await get_current_event(db)
    user = await _user(db, session)
    try:
        verification = await service.review_verification(
            db,
            event=event,
            verification_id=verification_id,
            reviewer=user,
            reviewer_role=_actor_role(session),
            approve=decision.approve,
            note=decision.note,
            allow_resubmit=decision.allow_resubmit,
        )
    except LookupError:
        raise _not_found("No such review.") from None
    except service.AlreadyDecided as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Somebody already decided this one.",
            http_status=status.HTTP_409_CONFLICT,
        ) from exc

    item = await service.review_item_view(db, store, verification=verification)
    await db.commit()
    return item
