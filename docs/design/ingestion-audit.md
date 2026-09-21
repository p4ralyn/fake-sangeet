# Ticket sheet audit — what ingestion has to cope with

Source: `FAKE SANGEET EARLY BIRD 2026-27.xlsx` and
`FAKE SANGEET_ PHASE 1 (Responses).xlsx`, read 21 Sep 2026.

No attendee data is reproduced here — only counts, formats and defect classes.

## Counts

| Source | Tickets | Singles | Duo/Couple | Seats |
|---|---:|---:|---:|---:|
| Early Bird · INFERNO | 58 | 10 | 48 | 106 |
| Early Bird · SNUPHORIA | 35 | 14 | 21 | 56 |
| Phase 1 (form) | 202 | 125 | 77 | 279 |
| **Total** | **295** | **149** | **146** | **441** |

The Early Bird workbook's own totals agree: SNUPHORIA 56, grand total 162.

**441 seats, not 442.** One seat unaccounted for against the stated figure.
Worth reconciling before anything is minted — a single phantom seat is cheap to
fix now and expensive to discover at the door.

### Capacity — this contradicts the brief

`EVENT_SEATS_TOTAL` is 600. Sold so far is **441**, so **159 seats remain**, not
the 50 the phase-3 plan assumes.

Either a phase-2 sheet exists that was not supplied, the 550 figure was an
estimate, or phase 3 is genuinely much larger than planned. Phase-3 inventory
must not be opened until this is settled: the `no_oversell` CHECK will refuse
to exceed 600, so the failure mode is buyers being turned away at checkout
rather than an oversold venue — but the wrong number still means either lost
sales or a sold-out screen nobody expects.

## Identity

**407 distinct people are identifiable. No NetID appears in both files**, so
the two sources can be ingested independently without cross-file collision.

Roughly 34 couple partners are *not* identifiable (see PROXY below) and have no
NetID anywhere.

## Defect classes ingestion must handle

### 1. Early Bird carries no email column at all

Columns are `SR.NO, NAME, SNUID, PHONE NO., TICKET, COST, ROLL NO., QR`. The
only identity is `SNUID`, in NetID form (two letters, three digits).

Since Google sign-in is restricted to `snu.edu.in`, the address is derivable as
`<snuid>@snu.edu.in`. That is safe *only because* the domain is fixed and the
NetID is the email local part — the same mapping the Google provider already
uses in reverse. Ingestion must construct it, not expect it.

### 2. Early Bird Duo rows pack two people into one cell

`SNUID` on a Duo row reads as two NetIDs joined by `&` or whitespace, and
`PHONE NO.` / `ROLL NO.` do the same. Split on `&`, `/`, `,` or whitespace and
match strictly against the NetID pattern — a loose split picks up fragments of
names and mints ghost tickets.

- **INFERNO**: 48 of 48 Duo rows carry **two** NetIDs → `SELF_CLAIM`.
- **SNUPHORIA**: all 21 Duo rows carry **one** NetID → `PROXY`. The partner is
  simply not recorded anywhere, so there is nobody to send a claim link to.

### 3. Excel has corrupted phone and roll numbers into floats

Many cells render as scientific notation (`9.87654321e9`) because Excel typed a
10-digit number as a float. Values are recoverable — a 10-digit integer is
exact in float64 — but the ingester must detect and normalise them rather than
storing the literal string. Rows where `&` forced the cell to text kept their
original formatting, so **both** shapes appear in the same column.

### 4. Twelve Phase-1 buyers cannot sign in as recorded

Of 202 buyer addresses: 190 valid `snu.edu.in`, and then

- 6 with no `@` at all
- 2 `gmail.com`
- 1 `snu.edu.un`, 1 `anu.edu.in`, 1 bare `snu` — typos
- 1 blank

Google sign-in is domain-restricted, so **all 12 are locked out and their pass
is unreachable**. These need human correction before or during ingestion. They
must surface as `ingestion_errors`, never be silently minted.

### 5. Partner data on couples

Of 77 Phase-1 couples: 76 have a partner name, **71 have a usable partner
email** → `SELF_CLAIM`. The remaining **6** (5 malformed, 1 blank) → `PROXY`.

Combined with Early Bird, the split is roughly:

- `SELF_CLAIM` — 119 couples (48 INFERNO + 71 Phase 1)
- `PROXY` — 27 couples (21 SNUPHORIA + 6 Phase 1)

### 6. Duplicate identities

- 1 duplicate NetID within Phase 1 buyers
- 1 duplicate NetID within Early Bird

`tickets_one_per_identity` will reject the second of each. That is the index
doing its job; the rows still need a human decision — refund, or a deliberate
second ticket under a different identity.

## What this means for M3

The sync must be **row-tolerant, not all-or-nothing**. A sheet of 295 rows with
~20 defective ones must mint 275 good tickets and write 20 `ingestion_errors`,
not fail wholesale. Re-running after corrections must be idempotent: the same
row must never mint a second ticket, keyed on a stable hash of the source row.

Per-row partner mode is decided at ingest from what the row actually contains,
per `docs/design/cohort-and-verification.md` — not from an event-wide setting.
