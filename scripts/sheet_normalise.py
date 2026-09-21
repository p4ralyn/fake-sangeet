"""Backwards-compatible alias for the sheet normaliser.

The rules themselves moved to `app.services.ingestion.normalise` at M3 — they
are domain logic, and CLAUDE.md puts domain logic in `services/`. This shim
stays so anything that already imports `sheet_normalise` keeps working; it adds
no behaviour of its own and must never grow any.
"""
from app.services.ingestion.normalise import (  # noqa: F401
    DOMAIN,
    LEADING_NETID_RE,
    NETID_RE,
    SPLIT_RE,
    Identity,
    TicketRow,
    normalise_identity,
    read_rows,
    recover_number,
    sheet_targets,
    split_packed,
)

__all__ = [
    "DOMAIN", "LEADING_NETID_RE", "NETID_RE", "SPLIT_RE",
    "Identity", "TicketRow", "normalise_identity", "read_rows",
    "recover_number", "sheet_targets", "split_packed",
]
