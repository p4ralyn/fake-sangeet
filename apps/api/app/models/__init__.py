"""Import every model so Base.metadata is complete for Alembic autogenerate."""

from app.models.access import (
    TERMINAL_PASS_STATES,
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
    GateType,
    InventoryLedger,
    LedgerReason,
)
from app.models.ingestion import (
    EMAIL_CLAIMABLE_STATUSES,
    EMAIL_TERMINAL_STATUSES,
    EmailOutbox,
    EmailStatus,
    IngestionError,
    TicketSource,
)
from app.models.ticketing import (
    TICKET_DEAD_STATUSES,
    TICKET_LIVE_STATUSES,
    Order,
    PartnerMode,
    Ticket,
    TicketStatus,
)
from app.models.user import EventStaff, StaffRole, User

__all__ = [
    "EMAIL_CLAIMABLE_STATUSES", "EMAIL_TERMINAL_STATUSES",
    "TERMINAL_PASS_STATES", "TICKET_DEAD_STATUSES", "TICKET_LIVE_STATUSES",
    "AuditLog", "EmailOutbox", "EmailStatus", "Event", "EventInventory", "EventStaff",
    "FunnelEvent", "Gate", "GateType", "IngestionError", "InventoryLedger",
    "LedgerReason", "Order", "PartnerMode", "Pass", "PassState",
    "Scan", "ScanAction", "ScanResult", "StaffRole", "Ticket", "TicketSource",
    "TicketStatus", "User",
]
