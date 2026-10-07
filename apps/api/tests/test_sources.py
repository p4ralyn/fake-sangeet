"""Which workbook tab becomes tickets.

The Phase 2 workbook carries the raw Form export as its FIRST tab, refunded and
unpaid buyers included, and the paid rows copied by hand into `final tickets`.
Phase 1's rule -- read the first tab -- would mint every refund. These build
tiny workbooks with the stdlib, so no attendee data and no openpyxl.
"""
import zipfile
from pathlib import Path

import pytest

from app.services.ingestion.sources import Phase1Source, Phase2Source, source_for_path

HEADER = [
    "Timestamp", "Email address", "Name", "Roll Number", "SNU Email (ab@snu.edu.in)",
    "Phone Number", "Which ticket will you be opting for?", "Partner's Name",
    "Partner's Roll Number", "Partner's SNU Email (ab@snu.edu.in)",
]
REFUNDED = ["t1", "", "Refunded Buyer", "", "rb111@snu.edu.in", "", "Single (Rs. 249)", "", "", ""]
PAID = [
    "t2", "", "Paid Buyer", "", "pb222@snu.edu.in", "", "Couple (Rs. 469)",
    "Pal", "", "pp333@snu.edu.in",
]


def _sheet_xml(rows: list[list[str]]) -> str:
    def cell(r: int, c: int, value: str) -> str:
        ref = f"{chr(ord('A') + c)}{r}"
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

    body = "".join(
        f'<row r="{r}">' + "".join(cell(r, c, v) for c, v in enumerate(row) if v) + "</row>"
        for r, row in enumerate(rows, start=1)
    )
    return (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )


def workbook(path: Path, tabs: dict[str, list[list[str]]]) -> Path:
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    sheets = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(tabs, 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        for i in range(1, len(tabs) + 1)
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>{sheets}</sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for i, rows in enumerate(tabs.values(), 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))
    return path


def test_a_phase2_workbook_mints_only_its_final_tickets_tab(tmp_path):
    path = workbook(tmp_path / "p2.xlsx", {
        "Form responses 1": [HEADER, REFUNDED, PAID],
        "final tickets": [HEADER, PAID],
        "email + refund": [HEADER, REFUNDED],
    })

    source = source_for_path(path)
    rows = source._parse()

    assert isinstance(source, Phase2Source)
    assert [r.buyer.net_id for r in rows] == ["pb222"]
    assert rows[0].source == "PHASE2"
    assert rows[0].partner.net_id == "pp333"


def test_a_phase2_workbook_without_the_tab_is_refused_not_read_from_the_first(tmp_path):
    path = workbook(tmp_path / "p2.xlsx", {"Form responses 1": [HEADER, REFUNDED]})

    with pytest.raises(ValueError, match="final tickets"):
        Phase2Source(path)._parse()


def test_a_single_tab_form_export_is_still_phase1(tmp_path):
    path = workbook(tmp_path / "p1.xlsx", {"Form Responses 1": [HEADER, REFUNDED]})

    source = source_for_path(path)

    assert type(source) is Phase1Source
    assert [r.buyer.net_id for r in source._parse()] == ["rb111"]
