"""What a NetID is. One definition, read by ingestion and by transfer.

Exactly two letters then three digits, e.g. pk944 — the client's rule (1 Oct
2026). It binds every identity the app accepts from a person or a sheet: a
ticket bound to a NetID nobody owns is a ticket nobody can sign in to collect.
"""
import re

NETID_RE = re.compile(r"^[a-z]{2}\d{3}$")


def is_net_id(value: str) -> bool:
    """True only for an already-cleaned value: lowercase, no spaces, no domain."""
    return bool(NETID_RE.fullmatch(value or ""))
