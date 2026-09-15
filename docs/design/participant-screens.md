# unigate — participant screens

Screen-by-screen behaviour and copy. Binding alongside `frontend-design-spec.md`
(visual system) and `frontend-api-contract.md` (what may be called).

Copy here is **final, not placeholder**. Use these strings verbatim. Sentence case
throughout. No `→` in any label. No manufactured urgency.

The flow order is binding (addendum §A2): choose type → details → hold → payment.
The payment screen does not exist without a live hold you own.

---

## 1. Invitation (`/`)

The landing. Ceremonial, centred, the one place that is purely an invitation.

**Hero is the invitation itself** — not a stat, not a gradient panel. Event name
in Marcellus at the top of the scale, framed above and below by a zari rule, with
the date set the way an invitation sets it and the venue beneath.

```
══════════════════════════════
        You're invited

        SANGAM NIGHT

     Friday, 14 November
      Main Auditorium
══════════════════════════════

     [ Reserve your seat ]

     42 of 150 seats left
```

Availability line, driven by `GET /api/v1/availability`, polled every 15s:

| Condition | Line | Button |
|---|---|---|
| plenty | `42 of 150 seats left` | Reserve your seat |
| `seats_available == 1` | `Only 1 seat left — singles only` | Reserve your seat |
| `seats_available == 0` | `All 150 seats are taken.` | Join the waitlist |

Signed out, the button still reads "Reserve your seat" and routes through sign-in
first — never show a separate "Sign in" call to action as the primary thing. The
person came to get a ticket, not to authenticate.

Never render a stale count as though it were live. While the first fetch is in
flight, show nothing rather than a zero.

---

## 2. Choose your ticket (`/buy`)

Left-aligned from here on — this is a working surface. Stepper visible: step 1 of 3.

Two options as cards, each with its price and what it includes. A type is offered
only while `seats_available >= type.seats` (addendum §A1.1).

```
Single          ₹499     1 seat
Couple          ₹899     2 seats · 2 QR codes, scanned separately
```

When COUPLE is unavailable, it is **disabled and explained in place**, never
hidden and never clickable-then-rejected:

> Only 1 seat left — singles only

A user who taps a live button and gets `SOLD_OUT` assumes the site is broken.

Couple, when chosen, states the consequence plainly beneath it:

> Two tickets, two QR codes. You and your partner are scanned in separately and
> can arrive or leave separately.

---

## 3. Your details (`/buy/details`)

Step 2 of 3. **No payment information appears on this screen.** That is the point
of the ordering — the data is collected before any money moves.

Fields: full name, NetID, phone, department, year. Prefilled from `/api/v1/me`
where known, all editable. For COUPLE, a second block: partner's name and partner's
email, with the mode explained:

> Your partner signs in with their own university account, uploads their own ID,
> and gets their own QR code. We'll email them a link.

Consent is a real checkbox, never pre-checked:

> I agree to the privacy notice and to my ID card being checked for entry.

Submit reads **Reserve your seat**. On success the hold exists and we move to
payment. On `409 SOLD_OUT`, do not proceed — replace the form's action area with:

> Those seats went while you were filling this in. Nothing has been charged.

and offer the waitlist. The user must never reach a payment screen for a seat
that does not exist.

---

## 4. Payment (`/buy/pay/:id`)

Step 3 of 3. Reachable only with a live hold. A direct visit that returns 403
routes silently back to step 1 — do not show an error for arriving somewhere you
were never allowed.

The countdown is the most important element on the screen. Tabular numerals,
turning `--haldi` at five minutes:

> Your seats are held until 21:14. Don't close this page.

Payment block, in this order:
1. **Amount**, large, copyable, exact: `₹899`
2. **UPI QR image**
3. **VPA** as copyable text
4. **Open your UPI app** — the `upi://` deep link, with the amount and the
   reference code already inside it, so nobody pays ₹499 for a couple ticket
5. **Reference code** `UG-7K3QD9`, with: *Add this to the payment note — it's how
   we match your payment.*

Below: **I've paid** continues to proof submission.

**On expiry**, the page self-expires client-side. It does not sit there looking
valid. Replace the payment block with:

> Your reservation expired. If you haven't paid, you can start again — seats may
> still be available. If you have already paid, submit your proof here and we'll
> review it.

Both actions offered. The second leads to the same proof form and produces a
`LATE_NO_SEAT` submission, which is routed to a human. People genuinely pay at
minute 21; without this their money is in the account with no record on the
platform and you find out over WhatsApp.

---

## 5. Submit your proof (`/buy/proof/:id`)

Fields: UTR / reference number (12 digits, validated client-side for shape only —
the server owns truth), screenshot (≤ 5 MB, image types), payer name and phone
prefilled and editable.

Client-side file guard before upload: type, size, and a readable preview. Rejecting
a 12 MB photo locally costs nothing; rejecting it after a slow mobile upload is a
support message.

On `409 UTR_ALREADY_USED`, surface it immediately and specifically — this is a
duplicate the server catches at insert time, not something the user waits hours to
learn:

> That reference number has already been submitted. Check you've copied the right
> one from your UPI app. If you think this is a mistake, ask a human.

Submit reads **Submit proof**.

---

## 6. Status (`/status/:id`)

One honest sentence about where things stand, and nothing that pretends to be
progress.

| State | Copy |
|---|---|
| `PENDING_APPROVAL` | `Submitted. We usually approve within a few hours — you'll get an email.` |
| `APPROVED` | `Approved. Your pass is ready.` with a link to the pass |
| `REJECTED`, resubmit allowed | the reason, then `You can submit new proof once.` |
| `REJECTED`, final | the reason, then `Ask a human` with the support contact |
| flagged `LATE_NO_SEAT` | `Received. The event filled up before this arrived, so a human is reviewing it personally.` |

Poll for state. Never show a fake progress bar for something a human is doing.

---

## 7. Your pass (`/pass`)

**The one bold element in the product.** Everything else has been quiet so this
can land.

A paper card with a zari border and a real perforation. Above the perforation:
holder name, ticket type, and for a couple, `Seat 1 of 2`. Below it, on the stub:
the QR and the short code in tabular numerals.

```
═══════════════════════════
   Aarav Sharma
   Couple · seat 1 of 2
╌╌╌╌╌ perforation ╌╌╌╌╌╌╌╌
       ███ QR ███
        7K3-QD9
═══════════════════════════
```

A couple ticket renders **two cards**, because it is two tickets, two QR codes,
counted separately at the gate.

The pass reveal is the single orchestrated motion in the app: the border draws,
then the QR resolves. Once, on first view, and never under
`prefers-reduced-motion: reduce`.

State line beneath each card, with an icon and a word, never colour alone:
`Not arrived · Inside · On a break · Departed`.

Offer **Add to home screen** guidance rather than a wallet pass — wallet passes
are explicitly out of scope for the dry run.

---

## 8. Waitlist (`/waitlist`)

Reached when seats are gone. Honest and short:

> All 150 seats are taken. Leave your details and we'll email you if one opens up.

Name, NetID, email, and the ticket type they wanted. No countdown, no false hope,
no "limited spots remaining".

---

## Cross-cutting

- **Signed out** anywhere that needs identity: route through sign-in preserving
  the intended destination, and return the person exactly where they were.
- **Errors** render `error.message` from the envelope and branch on `error.code`.
  Never show a raw code or a `request_id` to a participant — log it.
- **Loading**: skeletons only where the shape is known. Never a spinner over the
  whole page.
- **Empty states give direction.** "No pass yet" says how to get one.
- The **back button works** at every step. A person who backs out of payment and
  returns finds the same hold with the remaining time, never a second hold —
  `pi_one_live_per_user` guarantees one live intent per person server-side.
