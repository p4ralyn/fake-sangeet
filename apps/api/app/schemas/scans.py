"""Scanner request/response models.

**Two response models, selected by role — never one model filtered at runtime.**
Field-filtering is how PII leaks: a field added to the rich model silently
appears in the narrow one, and nothing fails. Here a field only reaches a
SECURITY volunteer if someone typed it into `SecurityScanResponse`.

`SecurityScanResponse` carries exactly LLD-v1 §11.5: name, ID photo URL, state,
break count, legal actions, decision token -- plus one client addition, the
guest tag (14 Oct 2026; every guest is a baarati). No NetID, no department,
no ticket reference, no pass id, no history.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.access import PassState, ScanAction
from app.models.ticketing import GuestTag
from app.services.access.scans import ScanOutcome


class ScanRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    gate_id: UUID | None = None
    device_id: UUID | None = None
    client_scan_uuid: UUID
    occurred_at: datetime
    offline_replay: bool = False


class ScanConfirmRequest(BaseModel):
    decision_token: str = Field(min_length=1, max_length=256)
    action: ScanAction


class SecurityScanResponse(BaseModel):
    """What a volunteer at the door sees. LLD-v1 §11.5, and nothing more."""

    status: Literal["DONE", "ACTION_REQUIRED"]
    duplicate: bool
    participant_name: str | None
    guest_tag: GuestTag | None
    id_image_url: str | None
    state: PassState
    breaks_used: int
    max_breaks: int
    legal_actions: list[ScanAction]
    decision_token: str | None
    decision_expires_in: int | None
    action: ScanAction | None

    @classmethod
    def from_outcome(cls, outcome: ScanOutcome) -> "SecurityScanResponse":
        return cls(
            status=outcome.status,
            duplicate=outcome.duplicate,
            participant_name=outcome.participant_name,
            guest_tag=outcome.guest_tag,
            id_image_url=outcome.id_image_url,
            state=outcome.state,
            breaks_used=outcome.breaks_used,
            max_breaks=outcome.max_breaks,
            legal_actions=outcome.legal_actions,
            decision_token=outcome.decision_token,
            decision_expires_in=outcome.decision_expires_in,
            action=outcome.action,
        )


class StaffScanResponse(SecurityScanResponse):
    """ADMIN and OWNER additionally get the identifiers they need to reconcile
    a scan against a pass, an order or an audit row."""

    pass_id: UUID
    short_code: str
    serial: int
    version: int
    from_state: PassState | None
    scan_id: UUID | None

    @classmethod
    def from_outcome(cls, outcome: ScanOutcome) -> "StaffScanResponse":
        narrow = SecurityScanResponse.from_outcome(outcome)
        return cls(
            **narrow.model_dump(),
            pass_id=outcome.pass_id,
            short_code=outcome.short_code,
            serial=outcome.serial,
            version=outcome.version,
            from_state=outcome.from_state,
            scan_id=outcome.scan_id,
        )
