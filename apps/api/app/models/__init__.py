"""Import every model so Base.metadata is complete for Alembic autogenerate."""

from app.models.access import (
    Pass,
    PassState,
    Scan,
    ScanAction,
    ScanResult,
)
from app.models.audit import AuditLog, FunnelEvent
from app.models.event import (
    Event,
    EventInventory,
    Gate,
    InventoryLedger,
    LedgerReason,
)
from app.models.ingestion import (
    EMAIL_CLAIMABLE_STATUSES,
    EmailOutbox,
    EmailStatus,
    IngestionError,
    TicketSource,
)
from app.models.ticketing import (
    MAX_TRANSFERS_PER_TICKET,
    TICKET_LIVE_STATUSES,
    GuestTag,
    Order,
    PartnerMode,
    Ticket,
    TicketStatus,
)
from app.models.user import EventStaff, StaffRole, User
from app.models.verification import (
    VERIFY_OPEN_OUTCOMES,
    IdImage,
    Registration,
    Verification,
    VerifyOutcome,
)

__all__ = [
    "EMAIL_CLAIMABLE_STATUSES", 
    "TICKET_LIVE_STATUSES",
    "VERIFY_OPEN_OUTCOMES",
    "MAX_TRANSFERS_PER_TICKET",
    "AuditLog", "EmailOutbox", "EmailStatus", "Event", "EventInventory", "EventStaff",
    "FunnelEvent", "Gate", "GuestTag", "IdImage", "IngestionError", "InventoryLedger",
    "LedgerReason", "Order", "PartnerMode", "Pass", "PassState", "Registration",
    "Scan", "ScanAction", "ScanResult", "StaffRole", "Ticket", "TicketSource",
    "TicketStatus", "User", "Verification", "VerifyOutcome",
]
