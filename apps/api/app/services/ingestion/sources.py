"""The `TicketSource` seam (LLD-v1 §7.1).

One port, several adapters. A source knows how to turn *its* medium into
`TicketRow`s and nothing else: it never touches the database, never decides
whether a row is mintable, and never counts a seat. That is the whole point —
when the organiser moves off a workbook and onto the Sheets API, a new subclass
lands here and `app/services/ingestion/service.py` does not change a line.

Two concrete sources today, both reading the client's `.xlsx` files through
`normalise.py`:

* `EarlyBirdSource`  — the hand-kept INFERNO / SNUPHORIA workbook. Identity is a
  NetID column, and Duo rows pack two NetIDs into one cell.
* `Phase1Source`     — the Google Form responses export. Identity is an email
  column, with a separate partner-email column.

`identity_is_email` is the difference that matters downstream. A bare NetID in
the Early Bird NetID column is the documented shape, not a defect; the same
bare NetID in Phase 1's "SNU Email" column is a row somebody typed wrong. The
ingest service reads this flag to decide whether a salvaged identity is worth an
admin's attention, so neither sheet is judged by the other's conventions.
"""
from __future__ import annotations

import asyncio
import zipfile
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import ClassVar

from app.services.ingestion.normalise import (
    Identity,
    TicketRow,
    normalise_identity,
    read_rows,
    recover_number,
    sheet_targets,
    split_packed,
)

# LLD-v1 §7.1's `source_type` vocabulary. Collapsed to what the dry run has:
# the other members arrive with their adapters, not before.
SOURCE_TYPE_XLSX = "csv_upload"


class TicketSource(ABC):
    """Yields normalised rows. Knows nothing about persistence."""

    source_type: ClassVar[str]
    #: True when the row's identity cell is meant to hold an email address.
    identity_is_email: ClassVar[bool]
    #: Stable logical name, used to build `orders.external_id`. Deliberately
    #: NOT the filename: renaming the workbook must not re-mint 295 tickets.
    source_key: ClassVar[str]

    @abstractmethod
    def fetch(self) -> AsyncIterator[TicketRow]:
        """Async-iterate every row the source can see, defective ones included.

        Filtering happens in the ingest service, which is the only place that
        can tell a defect from a duplicate.
        """
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> str:
        """One short line for the CLI banner. Never contains attendee data."""
        raise NotImplementedError


class XlsxTicketSource(TicketSource):
    """Shared plumbing for the two workbook shapes.

    Parsing is pure CPU on a zip archive, so it runs in a thread rather than
    blocking the loop (CLAUDE.md: never inline). The whole workbook is parsed
    once and then streamed — these files are tens of kilobytes, so there is
    nothing to gain from lazier reading and a lot to lose from holding a
    zipfile handle open across awaits.
    """

    source_type: ClassVar[str] = SOURCE_TYPE_XLSX

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"{self.source_key} ({self.source_type}) <- {self.path.name}"

    @abstractmethod
    def _parse(self) -> list[TicketRow]:
        raise NotImplementedError

    async def fetch(self) -> AsyncIterator[TicketRow]:
        for row in await asyncio.to_thread(self._parse):
            yield row


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _is_blank(row: Iterable[str]) -> bool:
    return not any((cell or "").strip() for cell in row)


class EarlyBirdSource(XlsxTicketSource):
    """`FAKE SANGEET EARLY BIRD 2026-27.xlsx` — INFERNO and SNUPHORIA tabs.

    Columns: SR.NO, NAME, SNUID, PHONE NO., TICKET, COST, ROLL NO., QR.
    There is no email column at all; `<snuid>@snu.edu.in` is the address, which
    is safe only because Google sign-in is pinned to that one domain.
    """

    identity_is_email: ClassVar[bool] = False
    source_key: ClassVar[str] = "EARLY_BIRD"

    #: Tabs that hold tickets. Anything else in the workbook (totals, notes, a
    #: NEEDS REVIEW tray written by scripts/audit_sheets.py) is ignored.
    TICKET_SHEETS: ClassVar[frozenset[str]] = frozenset({"INFERNO", "SNUPHORIA"})

    def _parse(self) -> list[TicketRow]:
        out: list[TicketRow] = []
        for sheet, target in sheet_targets(str(self.path)):
            if sheet not in self.TICKET_SHEETS:
                continue
            rows = read_rows(str(self.path), target)
            if not rows:
                continue
            index = {header: i for i, header in enumerate(rows[0]) if header}
            for number, row in enumerate(rows[1:], start=2):
                if _is_blank(row):
                    continue
                out.append(self._row(sheet, number, row, index))
        return out

    def _row(
        self, sheet: str, number: int, row: list[str], index: dict[str, int]
    ) -> TicketRow:
        raw_ids = _cell(row, index.get("SNUID"))
        ticket = _cell(row, index.get("TICKET")).lower()
        is_couple = ticket.startswith(("duo", "couple"))

        ids = split_packed(raw_ids)
        buyer = (
            normalise_identity(ids[0]) if ids else Identity(None, None, "UNUSABLE", raw_ids)
        )
        partner: Identity | None = None
        mode = "SELF_CLAIM"
        problems: list[str] = []
        if is_couple:
            if len(ids) >= 2:
                partner = normalise_identity(ids[1])
            else:
                # SNUPHORIA records only the buyer: there is nobody to send a
                # claim link to, so both passes stay on the buyer's device.
                mode = "PROXY"
                problems.append("couple row carries only one NetID")

        return TicketRow(
            source=f"{self.source_key}/{sheet}",
            source_row=number,
            ticket_type="COUPLE" if is_couple else "SINGLE",
            seats=2 if is_couple else 1,
            buyer=buyer,
            partner=partner,
            partner_mode=mode,
            phone=recover_number(_cell(row, index.get("PHONE NO."))),
            name=_cell(row, index.get("NAME")),
            problems=problems,
        )


class Phase1Source(XlsxTicketSource):
    """`FAKE SANGEET_ PHASE 1 (Responses).xlsx` — one Google Form export tab.

    Headers are long prose questions, so columns are found by substring, never
    by position: organisers reorder Form questions without telling anyone
    (LLD-v1 §7.2).
    """

    identity_is_email: ClassVar[bool] = True
    source_key: ClassVar[str] = "PHASE1"

    #: logical name -> a fragment that identifies its header, case-insensitive.
    COLUMNS: ClassVar[dict[str, str]] = {
        "name": "Name",
        "buyer": "SNU Email",
        "ticket": "which ticket",
        "partner": "Partner's SNU Email",
        "phone": "Phone Number",
    }

    def _parse(self) -> list[TicketRow]:
        sheets = sheet_targets(str(self.path))
        rows = read_rows(str(self.path), sheets[0][1])
        if not rows:
            return []
        header = rows[0]
        index = {
            key: next(
                (i for i, h in enumerate(header) if fragment.lower() in (h or "").lower()),
                None,
            )
            for key, fragment in self.COLUMNS.items()
        }
        if index["buyer"] is None or index["ticket"] is None:
            raise ValueError(
                f"{self.path.name}: no 'SNU Email' / 'which ticket' column found. "
                "The Form questions were renamed; update Phase1Source.COLUMNS."
            )

        out: list[TicketRow] = []
        for number, row in enumerate(rows[1:], start=2):
            if _is_blank(row):
                continue
            out.append(self._row(number, row, index))
        return out

    def _row(self, number: int, row: list[str], index: dict[str, int | None]) -> TicketRow:
        is_couple = "couple" in _cell(row, index["ticket"]).lower()
        buyer = normalise_identity(_cell(row, index["buyer"]))
        partner = normalise_identity(_cell(row, index["partner"])) if is_couple else None

        mode = "SELF_CLAIM"
        problems: list[str] = []
        if is_couple and (partner is None or not partner.usable):
            mode = "PROXY"
            problems.append("couple row carries no usable partner email")

        return TicketRow(
            source=self.source_key,
            source_row=number,
            ticket_type="COUPLE" if is_couple else "SINGLE",
            seats=2 if is_couple else 1,
            buyer=buyer,
            partner=partner,
            partner_mode=mode,
            phone=recover_number(_cell(row, index["phone"])),
            name=_cell(row, index["name"]),
            problems=problems,
        )


# Registry, not an if/else chain: a new workbook shape is a class plus one
# entry here. Order matters only in that the first match wins.
XLSX_SOURCES: tuple[type[XlsxTicketSource], ...] = (EarlyBirdSource, Phase1Source)


def source_for_path(path: str | Path) -> XlsxTicketSource:
    """Pick the adapter by what the workbook actually contains.

    Sniffing beats a `--format` flag the operator has to get right at 2 a.m.,
    and beats matching on the filename, which the client renames freely.
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"no such workbook: {target}")
    try:
        names = {name for name, _ in sheet_targets(str(target))}
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"{target.name} is not a readable .xlsx workbook") from exc

    if names & EarlyBirdSource.TICKET_SHEETS:
        return EarlyBirdSource(target)
    return Phase1Source(target)
