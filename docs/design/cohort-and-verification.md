# Cohorts, couple tickets and ID verification

Amends LLD-v2 §5 and addendum §A3. Binding.

## The two cohorts

| | Cohort A — sheet | Cohort B — phase 3 |
|---|---|---|
| Size | ~550 already sold offline | 50 seats remaining |
| Arrives via | Google Sheets ingestion (M3) | on-platform purchase (M5) |
| Price | already paid | SINGLE ₹299 · COUPLE ₹519 |
| ID card image | **collected after sign-in**, as its own step | **collected during checkout** |
| Payment proof | none — already paid | UTR + screenshot, admin-approved |

`total_seats` stays **600** — the real cap. The ~550 sold rows are ingested as
approved intents that each consume their seats, so `seats_available` falls out
of the addendum §A1 arithmetic and lands at ~50 on its own.

**Do not set `total_seats` to 50.** The gate headcount, the occupancy display
and the `inventory_ledger` reconciliation all read that number as the venue
capacity. Encoding "what's left on sale" there corrupts all three.

## ID verification — same requirement, different collection point

Every attendee needs a verified university ID before a pass issues. That does
not change. What changes is *when the image is captured*:

- **Cohort A** signs in with Google (`snu.edu.in`), and is then asked to upload
  their ID card. Nothing about them exists on the platform until they log in, so
  there is no earlier moment to ask.
- **Cohort B** uploads their ID card as part of checkout, alongside the payment
  screenshot. They are already in a form-filling flow; asking twice is friction
  for no gain.

So cohort B skips the *separate verification step*, not the verification. The
same `id_images` row and the same `verifications` row exist for both. At the
gate a volunteer cannot tell the cohorts apart, which is the property that
matters.

> **Stated assumption.** "They don't need to do this verification" is read as
> "they don't need the extra step, because the image was already taken", not as
> "their ID is never checked". If you actually want cohort B unverified, say so
> — it means a person can hold a pass with no ID on file, and the gate loses its
> only defence against a transferred ticket.

## Couple tickets — partner mode is per ticket, not per event

Addendum §A3 defines two modes and made them an event-wide config choice. They
now apply **per ticket**, decided by what the sheet row actually contains.

```
COUPLE row ingested from the sheet
   │
   ├─ partner details present (name + email)
   │     → SELF_CLAIM
   │       The couple ticket splits into two tickets. The partner gets a claim
   │       link, signs in with their OWN snu.edu.in account, uploads their OWN
   │       ID, and holds their own pass on their own device.
   │       This is the stronger path: each ID is checked against the identity
   │       that is actually standing at the gate.
   │
   └─ partner details missing
         → PROXY
           Both passes live on the buyer's device. The partner's ticket is not
           claimable, because there is nobody to claim it. Flag it visibly in
           the admin queue: that seat is vouched for by the buyer, not verified
           against an identity.
```

A `PROXY` ticket can be upgraded to `SELF_CLAIM` later if the buyer supplies
partner details — the reverse must not happen, because a claimed ticket is
already bound to a real identity.

### Schema implications (M2/M3)

- `tickets.partner_mode` — `SELF_CLAIM` | `PROXY`, set at mint from the row.
- `tickets.partner_of` — already specified in §A3; the second ticket points at
  the first.
- `tickets.claim_code` — only issued for `SELF_CLAIM`.
- `couple_partner_mode` in `events.config` becomes `"PER_TICKET"`, meaning the
  event does not impose a mode and each ticket carries its own.

### Gate behaviour is unchanged

Two tickets, two passes, two QR codes, scanned independently, occupancy +1 each.
Whether the partner claimed their own pass or the buyer is holding both on one
phone makes no difference to the scan logic — only to whose face the volunteer
is matching against whose ID.

## Sheet ingestion checks (M3)

The sync must surface, not silently absorb, these cases:

- COUPLE row with no partner details → mint as `PROXY`, flag for review.
- COUPLE row where the partner email equals the buyer's → reject the row; that
  is a data-entry error, and minting it would bind both tickets to one identity
  and trip `tickets_one_per_identity`.
- Partner email already holding a ticket of their own → do not mint; surface as
  an ingestion error. One person, one pass.
- Any email outside `snu.edu.in` → surface it. They cannot sign in with Google,
  so their pass would be unreachable.

Every one of these writes an `ingestion_errors` row rather than failing the
whole sync. A sheet of 550 must never be all-or-nothing.
