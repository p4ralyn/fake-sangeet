"""M3 ingest service — orders, tickets, seats, errors and queued mail.

Rows are constructed here rather than read from a workbook on purpose: the
workbook parsers have their own tests, and the client's real `.xlsx` files are
not in the repo. What is under test is what the *service* does with a row, so
the row is the fixture.

Every test that asserts something did NOT happen was proven by breaking the
guard and watching it fail (CLAUDE.md: tests that pass for the wrong reason have
appeared five times).
"""
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import (
    EmailOutbox,
    Event,
    EventInventory,
    GuestTag,
    IngestionError,
    InventoryLedger,
    Order,
    PartnerMode,
    Ticket,
    TicketStatus,
)
from app.models import TicketSource as TicketSourceRecord
from app.services.ingestion.normalise import Identity, TicketRow
from app.services.ingestion.service import (
    REASON_BUYER_TAKEN,
    REASON_BUYER_UNREADABLE,
    REASON_NO_SEATS,
    REASON_PARTNER_IS_BUYER,
    REASON_PARTNER_TAKEN,
    ingest_source,
    row_external_id,
)
from app.services.ingestion.sources import TicketSource

DOMAIN = "snu.edu.in"


def identity(net_id: str, *, status: str = "OK", raw: str | None = None) -> Identity:
    return Identity(net_id, f"{net_id}@{DOMAIN}", status, raw or f"{net_id}@{DOMAIN}")


def single(net_id: str, *, row: int = 2, name: str = "Aarav Sharma", phone: str = "9876543210"):
    return TicketRow(
        source="TEST",
        source_row=row,
        ticket_type="SINGLE",
        seats=1,
        buyer=identity(net_id),
        partner=None,
        partner_mode="SELF_CLAIM",
        phone=phone,
        name=name,
    )


def couple(
    net_id: str,
    partner: Identity | None,
    *,
    row: int = 2,
    mode: str = "SELF_CLAIM",
    problems: list[str] | None = None,
):
    return TicketRow(
        source="TEST",
        source_row=row,
        ticket_type="COUPLE",
        seats=2,
        buyer=identity(net_id),
        partner=partner,
        partner_mode=mode,
        phone="9876543210",
        name="Aarav Sharma",
        problems=problems or [],
    )


class FakeSource(TicketSource):
    """A source is a generator of rows and nothing else, so a list is a source."""

    source_type = "csv_upload"
    identity_is_email = False
    source_key = "TEST"

    def __init__(self, rows: list[TicketRow]) -> None:
        self.rows = rows

    async def fetch(self) -> AsyncIterator[TicketRow]:
        for row in self.rows:
            yield row

    def describe(self) -> str:
        return "fake source"


class EmailShapedSource(FakeSource):
    """Same rows, but the identity column is meant to hold an email — which is
    what decides whether a salvaged value is worth flagging."""

    identity_is_email = True
    source_key = "TEST_EMAIL"


@pytest_asyncio.fixture
async def event(db_session, event_id) -> Event:
    await db_session.commit()
    return await db_session.get(Event, event_id)


async def counts(db_session, event_id) -> dict[str, int]:
    async def count(model, *where):
        return await db_session.scalar(
            select(func.count()).select_from(model).where(model.event_id == event_id, *where)
        )

    inventory = await db_session.get(EventInventory, event_id)
    await db_session.refresh(inventory)
    return {
        "orders": await count(Order),
        "tickets": await count(Ticket),
        "errors": await count(IngestionError),
        "emails": await count(EmailOutbox),
        "ledger": await count(InventoryLedger),
        "seats": inventory.seats_committed,
    }


# ── the happy paths ──────────────────────────────────────────────────────────
async def test_single_row_mints_one_ticket_one_seat_one_email(db_session, event):
    report = await ingest_source(
        db_session, event=event, source=FakeSource([single("pk944")])
    )

    assert (report.orders_created, report.tickets_minted, report.seats_committed) == (1, 1, 1)
    assert report.emails_queued == 1
    assert await counts(db_session, event.id) == {
        "orders": 1, "tickets": 1, "errors": 0, "emails": 1, "ledger": 1, "seats": 1,
    }

    ticket = await db_session.scalar(select(Ticket))
    assert ticket.status is TicketStatus.PURCHASED
    assert ticket.bound_net_id == "pk944"
    assert ticket.partner_mode is None       # a single has no partner mode at all
    assert ticket.claim_code is None


async def test_couple_with_partner_splits_into_two_self_claim_tickets(db_session, event):
    report = await ingest_source(
        db_session, event=event, source=FakeSource([couple("pk944", identity("zq202"))])
    )

    assert (report.tickets_minted, report.seats_committed, report.emails_queued) == (2, 2, 2)
    tickets = (
        await db_session.scalars(select(Ticket).order_by(Ticket.seq_in_order))
    ).all()
    first, second = tickets
    assert [t.partner_mode for t in tickets] == [PartnerMode.SELF_CLAIM] * 2
    assert (first.bound_net_id, second.bound_net_id) == ("pk944", "zq202")
    # Only the partner ticket carries a claim code — the buyer's is already theirs.
    assert first.claim_code is None and second.claim_code
    assert second.partner_of == first.id

    order = await db_session.scalar(select(Order))
    assert order.quantity == 2 and order.ticket_type == "COUPLE"


async def test_every_ticket_minted_is_baarati(db_session, event):
    """14 Oct 2026: everybody is a baarati, both seats of a couple alike."""
    await ingest_source(
        db_session,
        event=event,
        source=FakeSource([single("pk944"), couple("zq202", identity("ms101"), row=3)]),
    )

    tags = set((await db_session.scalars(select(Ticket.guest_tag))).all())
    assert tags == {GuestTag.BAARATI}


async def test_proxy_couple_leaves_the_second_ticket_unbound_and_unclaimable(db_session, event):
    """No partner details means nobody to send a claim link to. Both passes stay
    on the buyer's device, and the seat is still counted."""
    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource(
            [couple("pk944", None, mode="PROXY", problems=["couple row carries only one NetID"])]
        ),
    )

    assert (report.tickets_minted, report.seats_committed) == (2, 2)
    assert report.emails_queued == 1              # only the buyer can be mailed
    assert report.rows_flagged == 1               # ...and an admin is told why

    second = await db_session.scalar(select(Ticket).where(Ticket.seq_in_order == 2))
    assert second.partner_mode is PartnerMode.PROXY
    assert second.bound_net_id is None
    assert second.claim_code is None

    error = await db_session.scalar(select(IngestionError))
    assert error.reason == "couple row carries only one NetID"
    assert error.row_ref == "TEST:2"


async def test_every_seat_movement_writes_a_ledger_row(db_session, event):
    await ingest_source(
        db_session,
        event=event,
        source=FakeSource([single("pk944", row=2), couple("zq202", identity("zq204"), row=3)]),
    )

    ledger = (await db_session.scalars(select(InventoryLedger).order_by(InventoryLedger.id))).all()
    assert [row.delta for row in ledger] == [1, 2]
    assert [row.reason for row in ledger] == ["INGEST", "INGEST"]
    assert [row.seats_committed_after for row in ledger] == [1, 3]
    # The ledger reconstructs the counter exactly.
    inventory = await db_session.get(EventInventory, event.id)
    await db_session.refresh(inventory)
    assert sum(row.delta for row in ledger) == inventory.seats_committed


async def test_the_source_record_is_created_once_and_stamped(db_session, event):
    source = FakeSource([single("pk944")])
    await ingest_source(db_session, event=event, source=source)
    await ingest_source(db_session, event=event, source=source)

    records = (await db_session.scalars(select(TicketSourceRecord))).all()
    assert len(records) == 1
    assert records[0].source_key == "TEST"
    assert records[0].last_synced_at is not None


# ── idempotency ──────────────────────────────────────────────────────────────
async def test_second_run_of_the_same_sheet_changes_nothing(db_session, event):
    rows = [single("pk944", row=2), couple("zq202", identity("zq204"), row=3)]

    first = await ingest_source(db_session, event=event, source=FakeSource(rows))
    after_first = await counts(db_session, event.id)
    second = await ingest_source(db_session, event=event, source=FakeSource(rows))

    assert first.orders_created == 2
    assert second.orders_created == 0
    assert second.tickets_minted == 0
    assert second.seats_committed == 0
    assert second.emails_queued == 0        # nobody gets a second "ticket ready"
    assert await counts(db_session, event.id) == after_first


async def test_a_repaired_row_mints_on_the_next_run_and_the_rest_do_not(db_session, event):
    """The case this is actually for: the client fixes the 4 NEEDS REVIEW rows
    and the ingester is run again over the whole workbook."""
    broken = TicketRow(
        source="TEST", source_row=3, ticket_type="SINGLE", seats=1,
        buyer=Identity(None, None, "UNUSABLE", "Rohan (no id)"),
        partner=None, partner_mode="SELF_CLAIM", name="Rohan",
    )
    await ingest_source(db_session, event=event, source=FakeSource([single("pk944"), broken]))
    assert (await counts(db_session, event.id))["orders"] == 1

    repaired = single("rn201", row=3, name="Rohan")
    second = await ingest_source(
        db_session, event=event, source=FakeSource([single("pk944"), repaired])
    )

    assert second.orders_created == 1       # exactly the repaired row
    assert second.tickets_minted == 1
    assert (await counts(db_session, event.id))["orders"] == 2


async def test_a_corrected_phone_updates_in_place_instead_of_minting_again(db_session, event):
    """Name and phone are the mutable columns (LLD-v1 §7.2). Changing one must
    not change the row hash, or a typo fix would mint a second ticket."""
    original = single("pk944", phone="98765", name="Aarav")
    corrected = single("pk944", phone="9876543210", name="Aarav Sharma")
    assert row_external_id("TEST", original) == row_external_id("TEST", corrected)

    await ingest_source(db_session, event=event, source=FakeSource([original]))
    report = await ingest_source(db_session, event=event, source=FakeSource([corrected]))

    assert (report.orders_created, report.orders_updated) == (0, 1)
    order = await db_session.scalar(select(Order))
    assert (order.purchaser_phone, order.purchaser_name) == ("9876543210", "Aarav Sharma")
    assert (await counts(db_session, event.id))["tickets"] == 1


async def test_the_row_hash_ignores_position_so_reordering_the_sheet_is_free(db_session, event):
    assert row_external_id("TEST", single("pk944", row=2)) == row_external_id(
        "TEST", single("pk944", row=97)
    )
    # ...but two different sheets holding the same person are two different rows,
    # which is what lets the duplicate be caught rather than silently merged.
    assert row_external_id("EARLY_BIRD", single("pk944")) != row_external_id(
        "PHASE1", single("pk944")
    )


# ── row tolerance ────────────────────────────────────────────────────────────
async def test_an_unreadable_buyer_is_trayed_and_the_batch_continues(db_session, event):
    broken = TicketRow(
        source="TEST", source_row=2, ticket_type="SINGLE", seats=1,
        buyer=Identity(None, None, "UNUSABLE", "???"),
        partner=None, partner_mode="SELF_CLAIM",
    )
    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource([broken, single("pk944", row=3), single("zq202", row=4)]),
    )

    assert report.rows_seen == 3
    assert report.rows_skipped == 1
    assert report.orders_created == 2       # the batch was not aborted
    assert report.halted_reason is None

    error = await db_session.scalar(select(IngestionError))
    assert error.reason == REASON_BUYER_UNREADABLE
    assert error.raw["buyer_raw"] == "???"  # the admin can see what to fix


async def test_the_same_open_problem_is_trayed_once_across_runs(db_session, event):
    broken = TicketRow(
        source="TEST", source_row=2, ticket_type="SINGLE", seats=1,
        buyer=Identity(None, None, "UNUSABLE", "???"),
        partner=None, partner_mode="SELF_CLAIM",
    )
    await ingest_source(db_session, event=event, source=FakeSource([broken]))
    second = await ingest_source(db_session, event=event, source=FakeSource([broken]))

    assert second.errors_recorded == 0
    assert (await counts(db_session, event.id))["errors"] == 1


async def test_a_partner_who_is_the_buyer_refuses_the_whole_row(db_session, event):
    """Minting it would bind both seats to one identity. Half-minting it would
    put the seat count out of step with a sheet that says two people paid."""
    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource([couple("pk944", identity("pk944"))]),
    )

    assert report.orders_created == 0
    assert report.rows_skipped == 1
    assert await counts(db_session, event.id) == {
        "orders": 0, "tickets": 0, "errors": 1, "emails": 0, "ledger": 0, "seats": 0,
    }
    error = await db_session.scalar(select(IngestionError))
    assert error.reason == REASON_PARTNER_IS_BUYER


async def test_one_identity_can_only_be_ticketed_once_across_sheets(db_session, event):
    """The real shape of this: the same NetID appears in Early Bird AND in the
    Phase 1 form. Two sheets, two hashes, one person — so the hash cannot catch
    it and the identity check has to."""
    await ingest_source(db_session, event=event, source=FakeSource([single("pk944")]))
    report = await ingest_source(
        db_session,
        event=event,
        source=EmailShapedSource([single("pk944", row=3, name="Someone Else")]),
    )

    assert report.orders_created == 0
    assert report.rows_skipped == 1
    assert (await counts(db_session, event.id))["seats"] == 1
    error = await db_session.scalar(select(IngestionError))
    assert error.reason == REASON_BUYER_TAKEN
    assert error.row_ref == "TEST:3"


async def test_the_same_person_twice_in_one_sheet_is_one_row_not_two(db_session, event):
    """Identical rows hash identically, so the duplicate is absorbed rather than
    trayed. That is the right outcome: it is the same purchase written twice."""
    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource([single("pk944", row=2), single("pk944", row=3)]),
    )

    assert report.rows_seen == 2
    assert report.orders_created == 1
    assert (await counts(db_session, event.id))["seats"] == 1


def _couple_with_names(name: str, partner_name: str, *, row: int = 2) -> TicketRow:
    return TicketRow(
        source="TEST", source_row=row, ticket_type="COUPLE", seats=2,
        buyer=identity("pk944"), partner=identity("zq202"), partner_mode="SELF_CLAIM",
        phone="9876543210", name=name, partner_name=partner_name,
    )


async def _greetings(db_session) -> dict[str, str]:
    rows = (await db_session.scalars(select(EmailOutbox))).all()
    for row in rows:
        await db_session.refresh(row)
    return {row.to_email: row.payload.get("name") for row in rows}


async def test_each_person_in_a_couple_is_greeted_by_their_own_name(db_session, event):
    """The partner used to read "Hi <buyer's name>," on an email meant for them."""
    await ingest_source(
        db_session, event=event,
        source=FakeSource([_couple_with_names("Aarav Sharma", "Priya Kapoor")]),
    )
    assert await _greetings(db_session) == {
        "pk944@snu.edu.in": "Aarav Sharma",
        "zq202@snu.edu.in": "Priya Kapoor",
    }


async def test_nobody_is_greeted_by_a_name_that_is_not_theirs(db_session, event):
    """A packed cell the initials could not untangle, and a partner with no
    name on the sheet, both get a plain "Hi," rather than somebody else's."""
    await ingest_source(
        db_session, event=event,
        source=FakeSource([_couple_with_names("Kittu & Priya", "")]),
    )
    assert await _greetings(db_session) == {
        "pk944@snu.edu.in": "",
        "zq202@snu.edu.in": "",
    }


async def test_a_re_ingest_corrects_the_greeting_on_mail_not_yet_sent(db_session, event):
    """The 418 queued emails were written with the old names. Re-running the
    corrected sheet fixes the ones still waiting and leaves sent ones alone."""
    await ingest_source(
        db_session, event=event, source=FakeSource([_couple_with_names("Aarav Sharma", "")])
    )
    buyer_mail = await db_session.scalar(
        select(EmailOutbox).where(EmailOutbox.to_email == "pk944@snu.edu.in")
    )
    buyer_mail.status = "SENT"  # this one already went: history, not ours to edit
    await db_session.commit()

    again = await ingest_source(
        db_session, event=event,
        source=FakeSource([_couple_with_names("Aarav S.", "Priya Kapoor")]),
    )

    assert again.orders_updated == 1
    assert await _greetings(db_session) == {
        "pk944@snu.edu.in": "Aarav Sharma",
        "zq202@snu.edu.in": "Priya Kapoor",
    }


async def test_a_demo_mint_queues_no_mail(db_session, event):
    """`scripts/demo_ticket.py` mints through this same path, but a demo seat
    must never ride along in the one-time send to every real attendee."""
    report = await ingest_source(
        db_session, event=event, source=FakeSource([single("pk944")]), queue_mail=False
    )

    assert (report.tickets_minted, report.seats_committed) == (1, 1)
    assert report.emails_queued == 0
    assert (await counts(db_session, event.id))["emails"] == 0


async def test_a_repeated_row_does_not_flip_the_stored_details_on_every_run(
    db_session, event
):
    """The real shape: `zz107` sent the same form three times with three
    different phone numbers. One order is right, but the copies used to take
    turns overwriting its phone, so every re-run reported "2 updated" over a
    sheet nobody had touched. The FIRST row in the sheet now wins, and a
    second run over the same sheet changes nothing."""
    rows = [
        single("pk944", row=2, phone="1111111111"),
        single("pk944", row=3, phone="2222222222"),
        single("pk944", row=4, phone="3333333333"),
    ]
    await ingest_source(db_session, event=event, source=FakeSource(rows))
    again = await ingest_source(db_session, event=event, source=FakeSource(rows))

    assert again.orders_updated == 0
    order = await db_session.scalar(select(Order))
    await db_session.refresh(order)
    assert order.purchaser_phone == "1111111111"


async def test_a_partner_who_already_holds_a_ticket_refuses_the_row(db_session, event):
    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource([single("zq202", row=2), couple("pk944", identity("zq202"), row=3)]),
    )

    assert report.orders_created == 1
    assert report.rows_skipped == 1
    assert (await counts(db_session, event.id))["seats"] == 1
    error = await db_session.scalar(select(IngestionError))
    assert error.reason == REASON_PARTNER_TAKEN


async def test_a_salvaged_identity_is_flagged_only_where_the_column_holds_emails(db_session, event):
    """A bare NetID in Early Bird's NetID column is the documented shape. The
    same value in Phase 1's email column is a row somebody typed wrong."""
    def salvaged(net_id: str) -> TicketRow:
        row = single(net_id)
        row.buyer = Identity(
            net_id, f"{net_id}@{DOMAIN}", "SALVAGED_BAD_DOMAIN", f"{net_id}@snu.edu.on"
        )
        return row

    quiet = await ingest_source(db_session, event=event, source=FakeSource([salvaged("pk944")]))
    assert quiet.rows_flagged == 0
    assert quiet.orders_created == 1

    loud = await ingest_source(
        db_session, event=event, source=EmailShapedSource([salvaged("zq202")])
    )
    assert loud.rows_flagged == 1
    assert loud.orders_created == 1
    error = await db_session.scalar(
        select(IngestionError).where(IngestionError.reason.like("buyer identity salvaged%"))
    )
    assert error is not None and "SALVAGED_BAD_DOMAIN" in error.reason


async def test_a_flagged_row_still_mints(db_session, event):
    """Flags tell an admin something; they never cost the attendee their seat."""
    row = single("pk944")
    row.buyer = Identity("pk944", f"pk944@{DOMAIN}", "SALVAGED_NO_DOMAIN", "pk944")
    report = await ingest_source(db_session, event=event, source=EmailShapedSource([row]))

    assert report.rows_flagged == 1
    assert (report.orders_created, report.tickets_minted, report.seats_committed) == (1, 1, 1)


# ── the cap ──────────────────────────────────────────────────────────────────
async def test_running_out_of_seats_halts_the_batch_rather_than_cherry_picking(
    db_session, event, event_id
):
    """A short batch is recoverable; a batch that admitted every single and
    dropped every couple is a reconciliation nightmare nobody would notice.

    Asserts through the `event_id` fixture rather than `event.id`: the halt
    rolls a transaction back, which expires every loaded ORM object.
    """
    inventory = await db_session.get(EventInventory, event.id)
    inventory.seats_total = 3
    await db_session.commit()

    report = await ingest_source(
        db_session,
        event=event,
        source=FakeSource(
            [
                couple("pk944", identity("zq202"), row=2),   # 2 seats -> fits
                couple("zq204", identity("ms101"), row=3),   # 2 seats -> does not
                single("zz999", row=4),                      # 1 seat -> would have fitted
            ]
        ),
    )

    assert report.halted is True
    assert REASON_NO_SEATS in report.halted_reason
    assert report.rows_seen == 2                 # row 4 was never looked at
    assert report.seats_committed == 2

    counted = await counts(db_session, event_id)
    assert counted["seats"] == 2 and counted["orders"] == 1
    error = await db_session.scalar(select(IngestionError))
    assert error.reason == REASON_NO_SEATS and error.row_ref == "TEST:3"


async def test_seats_never_exceed_the_cap_even_at_the_boundary(db_session, event):
    inventory = await db_session.get(EventInventory, event.id)
    inventory.seats_total = 2
    await db_session.commit()

    report = await ingest_source(
        db_session, event=event, source=FakeSource([couple("pk944", identity("zq202"))])
    )

    assert report.seats_committed == 2
    assert not report.halted
    refreshed = await db_session.get(EventInventory, event.id)
    await db_session.refresh(refreshed)
    assert refreshed.seats_committed == refreshed.seats_total


# ── what gets queued ─────────────────────────────────────────────────────────
async def test_the_queued_mail_carries_the_claim_code_only_for_the_partner(db_session, event):
    await ingest_source(
        db_session, event=event, source=FakeSource([couple("pk944", identity("zq202"))])
    )

    mail = (await db_session.scalars(select(EmailOutbox).order_by(EmailOutbox.to_email))).all()
    by_address = {str(m.to_email): m for m in mail}
    assert set(by_address) == {f"pk944@{DOMAIN}", f"zq202@{DOMAIN}"}
    assert by_address[f"pk944@{DOMAIN}"].payload["claim_code"] is None
    assert by_address[f"zq202@{DOMAIN}"].payload["claim_code"]
    assert all(m.status == "QUEUED" for m in mail)
    assert all(m.template == "ticket_ready" for m in mail)


@pytest.mark.parametrize("seq", [1, 2])
async def test_ticket_refs_are_derived_from_the_row_hash(db_session, event, seq):
    row = couple("pk944", identity("zq202"))
    await ingest_source(db_session, event=event, source=FakeSource([row]))

    expected = row_external_id("TEST", row)[:10].upper()
    ticket = await db_session.scalar(select(Ticket).where(Ticket.seq_in_order == seq))
    assert ticket.ticket_ref == f"FS26-{expected}-{seq}"
