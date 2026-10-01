"""Normalise the Fake Sangeet ticket workbooks into clean, ingestable rows.

Pure functions plus a thin xlsx reader. Moved here verbatim from
`scripts/sheet_normalise.py` at M3: the rules are domain logic, and domain logic
lives in the service layer, not in a script a container only sees because of a
bind mount. `scripts/sheet_normalise.py` is now a re-export of this module.

The source workbooks are never rewritten -- normalising at ingest keeps the
originals pristine and makes a re-run after corrections reproducible.

Defect classes handled (see docs/design/ingestion-audit.md):
  1. Early Bird carries no email; the NetID plus @snu.edu.in IS the address.
  2. Early Bird Duo rows pack two people into one cell, joined by & or space.
  3. Excel coerced phone/roll numbers into floats -> scientific notation.
  4. Some emails carry a typo'd domain, no domain, or no @ at all.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal

from defusedxml import ElementTree as ET

DOMAIN = "snu.edu.in"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Exactly two letters then three digits, e.g. pk944 — the client's rule (1 Oct
# 2026), and it now binds EVERY identity, including a local part on the real
# domain. It replaces the earlier "a local part on @snu.edu.in is the identity
# whatever its shape", which let `zzzz107` through: a ticket bound to a NetID
# nobody owns is a ticket nobody can sign in to collect.
NETID_RE = re.compile(r"^[a-z]{2}\d{3}$")
# A NetID glued to a domain ('zz106snu.edu.in'). The lookahead stops a fourth
# digit being silently dropped: 'ab1234...' is nobody, not ab123.
LEADING_NETID_RE = re.compile(r"^([a-z]{2}\d{3})(?!\d)")
# Any run of separators. Splitting aggressively is safe because every part
# is then filtered through NETID_RE -- fragments of names simply drop out.
SPLIT_RE = re.compile(r"(?:\s|&|,|/|\+|\band\b)+")


def recover_number(raw: str) -> str:
    """Undo Excel's float coercion. '9.876543210e9' -> '9876543210'.

    A 10-digit integer is exact in float64, so nothing is lost -- but the same
    column also holds plain strings (cells that contained '&' stayed text), so
    both shapes must survive this function.
    """
    v = (raw or "").strip()
    if not v:
        return ""
    if re.fullmatch(r"\d+", v):
        return v
    try:
        d = Decimal(v)
    except Exception:
        return re.sub(r"\D", "", v)
    if d == d.to_integral_value():
        return str(int(d))
    return re.sub(r"\D", "", v)


def split_packed(raw: str) -> list[str]:
    """Split a Duo cell holding two people. Matches NetIDs strictly.

    A loose split picks up fragments of names and would mint ghost tickets, so
    anything that does not look like a NetID is dropped rather than guessed at.
    """
    v = (raw or "").strip().lower()
    if not v:
        return []
    parts = []
    for raw_part in SPLIT_RE.split(v):
        # Trailing punctuation is common ('pk944. zq202'); strip it before
        # matching or the first NetID is silently discarded.
        cleaned = re.sub(r"[^a-z0-9]", "", raw_part)
        if cleaned:
            parts.append(cleaned)
    return [p for p in parts if NETID_RE.match(p)]


@dataclass
class Identity:
    net_id: str | None
    email: str | None
    status: str           # OK | SALVAGED_* | UNUSABLE | EMPTY
    raw: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.net_id)


def normalise_identity(raw: str) -> Identity:
    """Resolve one cell into a NetID and the address it implies.

    Whatever the cell holds, the result is either a NetID matching NETID_RE or
    nothing. A value on the real domain is trusted as written only when its
    local part is already that shape; anything rescued from a typo is marked
    SALVAGED_* so the row is flagged for an admin to glance at.
    """
    v = (raw or "").strip().lower()
    if not v:
        return Identity(None, None, "EMPTY", raw)

    if v.endswith("@" + DOMAIN):
        local = v.split("@")[0]
        if not local:
            return Identity(None, None, "EMPTY", raw)
        cleaned = re.sub(r"\s", "", local)               # 'zz103 @snu.edu.in'
        if not NETID_RE.match(cleaned):
            return Identity(None, None, "UNUSABLE", raw)
        status = "OK" if cleaned == local else "SALVAGED_WHITESPACE"
        return Identity(cleaned, f"{cleaned}@{DOMAIN}", status, raw)

    if "@" not in v:
        squashed = re.sub(r"[^a-z0-9]", "", v)
        if NETID_RE.match(squashed):                       # bare netid
            return Identity(squashed, f"{squashed}@{DOMAIN}", "SALVAGED_NO_DOMAIN", raw)
        m = LEADING_NETID_RE.match(squashed)               # netid glued to a domain
        if m:
            return Identity(m.group(1), f"{m.group(1)}@{DOMAIN}", "SALVAGED_GLUED", raw)
        return Identity(None, None, "UNUSABLE", raw)

    local = re.sub(r"\s", "", v.split("@", 1)[0])
    if NETID_RE.match(local):                              # right person, wrong domain
        return Identity(local, f"{local}@{DOMAIN}", "SALVAGED_BAD_DOMAIN", raw)
    return Identity(None, None, "UNUSABLE", raw)


# ── minimal xlsx reader (stdlib only) ────────────────────────────────────────
def _col_index(ref: str) -> int:
    m = re.match(r"([A-Z]+)", ref or "A")
    n = 0
    for ch in m.group(1) if m else "A":
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def sheet_targets(path: str) -> list[tuple[str, str]]:
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sh in wb.iter(f"{NS}sheet"):
        tgt = rmap[sh.get(f"{RNS}id")].lstrip("/")
        # Writers differ: Excel emits 'worksheets/sheet1.xml' relative to xl/,
        # openpyxl emits '/xl/worksheets/sheet1.xml' absolute. Normalise both,
        # or a workbook round-tripped through openpyxl yields 'xl/xl/...'.
        if not tgt.startswith("xl/"):
            tgt = "xl/" + tgt
        out.append((sh.get("name"), tgt))
    return out


def read_rows(path: str, target: str) -> list[list[str]]:
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in ss.iter(f"{NS}si")]
    sheet = ET.fromstring(z.read(target))
    out = []
    for row in sheet.iter(f"{NS}row"):
        cells: dict[int, str] = {}
        for c in row.iter(f"{NS}c"):
            v = c.find(f"{NS}v")
            t = c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{NS}t"))
            elif v is None:
                val = ""
            elif t == "s":
                val = shared[int(v.text)]
            else:
                val = v.text or ""
            cells[_col_index(c.get("r"))] = val.strip()
        if cells:
            out.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return out


@dataclass
class TicketRow:
    source: str
    source_row: int
    ticket_type: str                 # SINGLE | COUPLE
    seats: int
    buyer: Identity
    partner: Identity | None
    partner_mode: str                # SELF_CLAIM | PROXY
    phone: str = ""
    name: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ingestable(self) -> bool:
        return self.buyer.usable
