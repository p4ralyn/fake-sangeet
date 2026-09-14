"""Import every model so Base.metadata is complete for Alembic autogenerate."""

from app.models.audit import AuditLog, FunnelEvent
from app.models.event import Event, EventInventory, Gate, GateType
from app.models.user import EventStaff, StaffRole, User

__all__ = [
    "AuditLog", "Event", "EventInventory", "EventStaff",
    "FunnelEvent", "Gate", "GateType", "StaffRole", "User",
]
