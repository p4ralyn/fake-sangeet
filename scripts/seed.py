#!/usr/bin/env python
"""Seed the single event, its inventory, one gate and the staff NetIDs.

Idempotent: safe to run on every deploy. Never touches `seats_committed`.
"""
import asyncio
import sys
from datetime import datetime

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_factory
from app.models import Event, EventInventory, EventStaff, Gate, StaffRole


class SeedConfig(BaseSettings):
    """Pydantic at every boundary — including a script's boundary."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    event_slug: str = "dryrun"
    event_name: str = "Collab Night"
    event_venue: str = "Main Auditorium"
    event_timezone: str = "Asia/Kolkata"
    event_starts_at: datetime
    event_ends_at: datetime
    event_seats_total: int = 150
    event_gate_name: str = "Main Gate"
    event_max_breaks: int = 1

    seed_owner_net_ids: list[str] = Field(default_factory=list)
    seed_admin_net_ids: list[str] = Field(default_factory=list)
    seed_security_net_ids: list[str] = Field(default_factory=list)

    def event_config(self) -> dict:
        """Addendum §A1 shape. No `phases[]`, no `hard_cap` (Phase 0 decisions 2.2, 2.3).

        Phase 3 pricing. Note COUPLE is not 2x SINGLE -- 519 against 598 -- so
        never derive the couple price by doubling; read it from this config.

        `total_seats` stays the REAL cap (600), not the number still on sale.
        The ~550 already sold offline are ingested as approved rows that each
        consume their seats, so `seats_available` falls out of the addendum A1
        arithmetic instead of being hardcoded. Setting total_seats to 50 would
        break the gate headcount and the ledger reconciliation.
        """
        return {
            "capacity": {"total_seats": self.event_seats_total},
            "ticket_types": [
                {"code": "SINGLE", "label": "Single", "seats": 1, "price": 299, "active": True},
                {"code": "COUPLE", "label": "Couple", "seats": 2, "price": 519, "active": True},
            ],
            "hold_ttl_minutes": 20,
            "hold_grace_minutes": 10,
            # LLD-v1 §11.1. One break per pass: EXIT_BREAK is refused once
            # `breaks_used >= max_breaks`, and the gate then offers CHECK_OUT
            # only. Lives here, not in Settings, because it is per-event policy.
            "access": {"max_breaks": self.event_max_breaks},
            # Per-TICKET now, not per-event: a sheet row that carries partner
            # details self-claims; one that does not stays proxied to the buyer.
            # See docs/design/cohort-and-verification.md.
            "couple_partner_mode": "PER_TICKET",
        }

    def staff_grants(self) -> list[tuple[str, StaffRole]]:
        grants = [
            *[(n, StaffRole.OWNER) for n in self.seed_owner_net_ids],
            *[(n, StaffRole.ADMIN) for n in self.seed_admin_net_ids],
            *[(n, StaffRole.SECURITY) for n in self.seed_security_net_ids],
        ]
        # Idempotency by construction, not by session autoflush behaviour: collapse
        # exact (net_id, role) duplicates here so the caller's loop never sees the
        # same pair twice. A NetID repeated across *different* role lists is a
        # legitimate distinct grant and must survive this dedupe.
        return list(dict.fromkeys(grants))


async def seed(session: AsyncSession, config: SeedConfig) -> dict[str, int]:
    created = {"events": 0, "gates": 0, "staff": 0}

    event = await session.scalar(select(Event).where(Event.slug == config.event_slug))
    if event is None:
        event = Event(
            slug=config.event_slug,
            name=config.event_name,
            venue=config.event_venue,
            timezone=config.event_timezone,
            starts_at=config.event_starts_at,
            ends_at=config.event_ends_at,
            status="LIVE",
            config=config.event_config(),
        )
        session.add(event)
        await session.flush()
        created["events"] = 1
    else:
        event.name = config.event_name
        event.venue = config.event_venue
        event.starts_at = config.event_starts_at
        event.ends_at = config.event_ends_at
        event.config = config.event_config()

    inventory = await session.get(EventInventory, event.id)
    if inventory is None:
        session.add(EventInventory(event_id=event.id, seats_total=config.event_seats_total))
    else:
        # Capacity may be adjusted; committed seats are live state and are never reset.
        inventory.seats_total = config.event_seats_total

    gate = await session.scalar(
        select(Gate).where(Gate.event_id == event.id, Gate.name == config.event_gate_name)
    )
    if gate is None:
        session.add(Gate(event_id=event.id, name=config.event_gate_name))
        created["gates"] = 1

    for net_id, role in config.staff_grants():
        existing = await session.scalar(
            select(EventStaff).where(
                EventStaff.event_id == event.id,
                EventStaff.net_id == net_id,
                EventStaff.role == role,
            )
        )
        if existing is None:
            session.add(EventStaff(event_id=event.id, net_id=net_id, role=role))
            created["staff"] += 1
        elif not existing.is_active:
            existing.is_active = True

    await session.flush()
    return created


async def main() -> None:
    config = SeedConfig()
    async with session_factory() as session:
        created = await seed(session, config)
        await session.commit()
    print(f"seed complete: {created}")  # noqa: T201


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
