# unigate — LLD v2 Addendum A: Seat Inventory, Ticket Types, Payment Gating & ID Card Signals

**Applies to:** `unigate-LLD-v2-single-event.md`. Replaces §3 (phases/inventory) and §4.1 (purchase flow), and amends §6 (schema), §7 (API) and §9.3 of v1 (OCR scoring).

---

## A1. Inventory is counted in **seats**, not orders

The cap is a headcount. A couple ticket consumes 2 seats, a single consumes 1. `150` means 150 bodies through the door in any mix:

```
50 couples (100) + 50 singles (50)   = 150 ✅
30 couples (60)  + 90 singles (90)   = 150 ✅
75 couples (150) + 0   singles       = 150 ✅
```

```jsonc
// events.config
{
  "capacity": { "total_seats": 150, "hard_cap": 150 },
  "ticket_types": [
    { "code": "SINGLE", "label": "Single",  "seats": 1, "price": 499,  "active": true },
    { "code": "COUPLE", "label": "Couple",  "seats": 2, "price": 899,  "active": true }
  ],
  "hold_ttl_minutes": 20,
  "hold_grace_minutes": 10
}
```

**The invariant, stated once and enforced in one place:**

```
seats_committed = Σ seats(t) for every payment_intent in
                  {HOLD (unexpired), PENDING_APPROVAL, APPROVED}
seats_available = total_seats − seats_committed
```
`PENDING_APPROVAL` counts against the cap. This is the choice that matters: a submitted-but-unapproved payment must hold its seats, or you will approve 150 seats' worth of screenshots and discover you sold 190.

### A1.1 Availability display
The buy page shows availability **per type**, derived from the same number:
- `SINGLE` is offered while `seats_available >= 1`
- `COUPLE` is offered while `seats_available >= 2`

So the last remaining seat sells as a single only, and `COUPLE` greys out with "Only 1 seat left — singles only". Never show a type you can't actually grant; a user who taps a live button and gets `SOLD_OUT` assumes the site is broken.

### A1.2 Atomicity — the only correct way to do this
Do **not** compute availability with `SELECT count(*)` and then insert. Two requests will read 149 at the same time and both commit. The counter lives in one row, mutated under a lock, in the same transaction as the hold:

```sql
BEGIN;
  SELECT seats_total, seats_committed
    FROM event_inventory
   WHERE event_id = $1
     FOR UPDATE;                                  -- serialises every buyer, one row

  -- app checks: seats_committed + $seats <= seats_total   → else raise SOLD_OUT

  INSERT INTO payment_intents (…, ticket_type, seats, state, hold_expires_at)
       VALUES (…, $type, $seats, 'HOLD', now() + interval '20 minutes');

  UPDATE event_inventory
     SET seats_committed = seats_committed + $seats,
         updated_at = now()
   WHERE event_id = $1;
COMMIT;
```

`FOR UPDATE` on a single row briefly serialises purchases. At 150 seats and a transaction that takes ~2 ms, that ceiling is roughly 500 holds/second — vastly more than you need, and it is *exactly* correct under any amount of concurrency. Redis stays a read-through cache for the displayed number (10 s TTL, invalidated on every commit); **Postgres is the only source of truth for the decision.**

Seats are returned by decrementing `seats_committed` in the same locked pattern on: hold expiry sweep, payment rejection (final), cancellation, and admin release. Every release writes an `inventory_ledger` row so a drifting counter can be reconstructed and audited.

```sql
CREATE TABLE event_inventory (
  event_id       uuid PRIMARY KEY REFERENCES events(id),
  seats_total    int NOT NULL CHECK (seats_total > 0),
  seats_committed int NOT NULL DEFAULT 0 CHECK (seats_committed >= 0),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT no_oversell CHECK (seats_committed <= seats_total)   -- last line of defence
);

CREATE TABLE inventory_ledger (
  id bigserial PRIMARY KEY,
  event_id uuid NOT NULL,
  intent_id uuid REFERENCES payment_intents(id),
  delta int NOT NULL,                     -- +1/+2 on hold, −1/−2 on release
  reason text NOT NULL,                   -- HOLD|EXPIRE|REJECT|CANCEL|ADMIN_RELEASE|FORCE_APPROVE
  seats_committed_after int NOT NULL,
  actor_user_id uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
```
The `no_oversell` CHECK constraint means that even if application logic is wrong, the database physically refuses to oversell. That is worth more than any amount of careful code.

**Admin override:** `force_approve` may exceed the cap (you will want this once). It requires a typed confirmation, writes `reason='FORCE_APPROVE'`, and raises `seats_total` rather than letting `seats_committed` exceed it — so the constraint stays true and the override is visible in the ledger forever.

---

## A2. Order of operations: details first, payment last

**New rule: the payment page does not exist until the details are filled and a hold is granted.**

```
VISITOR logs in (Microsoft SSO)
   ▼
[1] Choose ticket type          ← availability computed live; unavailable types disabled
   ▼
[2] Details form                ← name, NetID, phone, dept/year, +partner block if COUPLE,
   │                              consent checkbox. NO payment info visible on this page.
   │  POST /api/v1/payments/hold  { ticket_type, details }
   │      ├─ seats available → 201, intent=HOLD, 20:00 countdown starts
   │      └─ not available   → 409 SOLD_OUT  ← the user never reaches a payment screen
   ▼
[3] Payment page                ← ONLY reachable with a live HOLD you own.
   │                              Shows UPI QR, VPA, exact amount, countdown timer.
   │                              Direct GET without a hold → 302 back to step 1.
   ▼
[4] Submit UTR + screenshot     → HOLD → PENDING_APPROVAL (seats stay committed)
   ▼
[5] Admin approves              → tickets minted (1 or 2) → ID verification funnel
```

This ordering does three things at once: it collects the data you need *before* money moves, it stops anyone from paying into a sold-out event, and it gives you a real record of who abandoned at which step.

### A2.1 "Someone already on the payment page and paying — only that counts"
Exactly the behaviour a hold gives you. Precise semantics:

| Situation | Behaviour |
|---|---|
| User is on the payment page with an unexpired hold | Their seats are **already committed**. Cap being reached elsewhere cannot touch them. They complete normally. |
| Another user arrives after the last seats are held | `409 SOLD_OUT` at step 2. They never see the QR. Offered the waitlist. |
| Hold expires while the user is mid-payment | Seats are released — but a **10-minute grace window** follows. If they submit within grace **and** seats are still available, the hold is re-granted silently. If seats are gone, they see: "Your reservation expired and the event is now full. If you have already paid, submit your proof here and we'll review it." → intent is created in `PENDING_APPROVAL` with flag `LATE_NO_SEAT`, **outside** the inventory count, routed to you for a force-approve or a refund. |
| User abandons without paying | Sweeper releases seats at expiry + grace. Seats return to the pool. |
| User refreshes / reopens the payment page | Same hold resumes with the remaining countdown. Refresh must never mint a second hold — `pi_one_live_per_user` guarantees it. |

**The `LATE_NO_SEAT` path is not optional.** People genuinely pay at minute 21. Without it their money is in your account with no record on the platform, and you find out over WhatsApp.

### A2.2 UI details that prevent support tickets
- Countdown timer visible on the payment page, turning amber at 5 minutes, with "Don't close this page — your seats are reserved until 21:14."
- The exact amount is shown as copyable text *and* encoded in the UPI deep link (`upi://pay?pa=…&am=899&tn=…`) so nobody pays ₹499 for a couple ticket.
- A per-intent reference code (`UG-7K3QD9`) shown on the payment page and pre-filled into the UPI note field — makes your bank reconciliation dramatically easier.
- After expiry, the page self-expires client-side and tells them what to do; it does not sit there looking valid.

---

## A3. Couple tickets → two attendees, two passes

A couple ticket is **one payment, two seats, two tickets, two QR codes**. Each attendee is scanned, verified and counted independently — otherwise your occupancy number and your exit/re-entry logic both break the moment one of them steps out for a break.

```
order (COUPLE, seats=2, one payment_intent)
  ├── ticket #1  → primary buyer  (auto-bound to the purchasing identity)
  └── ticket #2  → partner        (claim_code, invite emailed)
```

**Partner onboarding — two supported modes**, pick per event in config (`couple_partner_mode`):

| Mode | How it works | Use when |
|---|---|---|
| `SELF_CLAIM` *(default)* | Partner's email captured in the details form; they get a claim link, log in with their own SSO, upload their own ID, get their own QR. | Both are university students. Cleanest: each ID card is verified against its own logged-in identity, which is the whole strength of the design. |
| `PROXY` | Buyer supplies the partner's name + uploads the partner's ID card in the same flow. Partner's ticket is force-routed to `MANUAL_REVIEW` (no SSO identity to match against) and the QR is emailed to the buyer. | Partner is from another college / has no NetID. |

`PROXY` is the honest answer to the outside-attendee problem for this event — flag those registrations visibly in the admin queue, because they are verified by you, not by SSO.

**Gate behaviour:** two independent passes, two scans, occupancy +1 each. They can arrive separately, break separately, leave separately. The scanner shows "Couple ticket — partner: Aarav S. (checked in 19:42)" as context, which helps at the door without changing any rule.

**Schema:** `payment_intents` gains `ticket_type text NOT NULL` and `seats int NOT NULL CHECK (seats BETWEEN 1 AND 2)`. `tickets` gains `ticket_type` and `partner_of uuid NULL REFERENCES tickets(id)`. Approval mints `seats` tickets in one transaction.

---

## A4. ID card verification — signals for your actual card

The card carries **name, roll number, university branding and logo**. That's three independent signals plus a template, which is a strong combination. Revised scoring (replaces v1 §9.3):

| Signal | Weight | Method | Notes |
|---|---|---|---|
| Roll number matches the authenticated identity | **0.35** | normalise → exact; partial credit at Levenshtein ≤1 | OCR reliably confuses `0/O`, `1/I/l`, `5/S`, `8/B`, `2/Z`. Normalise those *before* comparing, and derive the roll number from the NetID (or from the sheet row) — you must know what to compare against. |
| Name matches SSO display name | **0.30** | RapidFuzz `token_set_ratio` after lowercasing, stripping honorifics, initials and diacritics | Handles "Aarav Kumar Sharma" vs "Sharma Aarav K." — reorderings are the common case, not a mismatch. |
| University name / branding text present | **0.15** | fuzzy contains against `institution_aliases` (full name, common abbreviation, campus name) | |
| **Logo template match** | **0.10** | ORB/SIFT keypoint match against a reference crop of the logo, or a colour-histogram + template correlation on the expected region | The cheap, high-value addition: it's what separates a real card from a typed-up fake or a library card. Also catches a photo of a *screen* showing a card, since the logo's colour profile shifts. |
| Photo region present | **0.05** | OpenCV face-region detector (presence only, no recognition) | Rejects timetables, fee receipts, screenshots of text. |
| Image quality composite | **0.05** | blur / glare / resolution | Already gated client-side; this is the backstop. |

**Decision thresholds (unchanged in shape):**
```
score ≥ 0.85 AND roll_exact AND no fraud flag        → AUTO_PASS
0.60 ≤ score < 0.85, or roll matched only fuzzily    → MANUAL_REVIEW
score < 0.60 and attempts < 3                        → RETRY_REQUIRED (specific reason, §1)
score < 0.60 and attempts = 3                        → MANUAL_REVIEW
```

Two practical notes on the roll number, because this is where accuracy is won:
1. **Put the regex in config, and build the tester first.** `id_patterns: ["^\\d{2}[A-Z]{3}\\d{4}$", …]`. Collect 20 real cards before M4 and tune against them; a pattern that matches your cohort turns the roll-number signal from noisy to near-perfect.
2. **Crop by region, not just full-text search.** Once you have 20 samples, the roll number and name sit in predictable zones of a standardised university card. Run OCR on the whole card, but score region-anchored hits higher than a loose match anywhere in the text — that's what stops the OCR reading a roll number off a fee stamp in the corner.

Store the logo reference crop in config (`branding.logo_reference_object`) so this is tunable without a redeploy.

---

## A5. API delta

| Method | Path | Change |
|---|---|---|
| GET | `/api/v1/availability` | **new** — `{ seats_total, seats_available, types: [{code, label, price, seats, available: bool}] }`. Redis-cached 10 s. Polled by the buy page every 15 s. |
| POST | `/api/v1/payments/hold` | **changed** — now requires the full details payload and `ticket_type`; performs the locked inventory transaction; returns `hold_expires_at`. `409 SOLD_OUT` when seats are gone. |
| GET | `/api/v1/payments/{id}/page` | **new** — 403/redirect unless the caller owns a live `HOLD`. This is the gate on the payment screen. |
| POST | `/api/v1/payments/{id}/submit` | **changed** — accepts submissions inside the grace window; sets `LATE_NO_SEAT` when seats could not be re-granted. |
| POST | `/api/v1/admin/payments/{id}/approve` | **changed** — mints `seats` tickets; `?force=true` raises `seats_total` and writes a ledger row. |
| POST | `/api/v1/admin/inventory/adjust` | **new**, OWNER only — change `seats_total` (e.g. venue grants 20 more), ledger-logged. |
| GET | `/api/v1/admin/inventory/ledger` | **new** — the audit trail for every seat movement. |

New error codes: `SOLD_OUT · TYPE_UNAVAILABLE · HOLD_REQUIRED · HOLD_EXPIRED · LATE_NO_SEAT`.

---

## A6. Tests that must exist before you sell a single ticket

1. **Concurrency:** 200 parallel hold requests against 150 seats → exactly 150 seats committed, 0 oversold, no deadlocks. Run it; don't reason about it.
2. **Mixed types:** interleaved single/couple requests never exceed the cap, and the last remaining single seat is grantable while couple is refused.
3. **Release paths:** expiry sweep, rejection, cancellation each return exactly the right seat count; `seats_committed` always equals `Σ ledger.delta`.
4. **Hold ownership:** a second user cannot open another user's payment page; a refresh does not mint a second hold.
5. **Grace window:** submission at T+21 min with seats available succeeds; with seats gone it lands as `LATE_NO_SEAT` and is *not* counted against inventory.
6. **Couple minting:** approval creates exactly 2 tickets, 2 passes, 2 QR emails, and occupancy counts them separately through a full check-in / break / check-out cycle.
7. **The constraint:** attempt a deliberate oversell with the application check disabled — the `no_oversell` CHECK must reject it.
