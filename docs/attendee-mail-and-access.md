# The "ticket is ready" email, and how an attendee gets to their pass

What every ticket holder receives, word for word, and what they do after it.
Taken from the code (`apps/api/app/services/mailer/templates.py`), not written
from memory — if the template changes, change this file with it.

**Status (5 Oct 2026): nothing has been sent.** 418 of these sit QUEUED in the
outbox and the `mailer` service is stopped on purpose. The client's decision is
one send to everybody once the phase-2 sheet is ingested.

---

## Who gets one

One email per **person with a bound ticket**, at `<netid>@snu.edu.in`:

| Ticket | Emails |
|---|---|
| Single | 1, to the buyer |
| Couple, partner named on the sheet (SELF_CLAIM, 125 couples) | 2 — the buyer, and the partner |
| Couple, no partner on the sheet (PROXY, 21 couples, all Snuphoria Early Bird) | 1, to the buyer only. Nobody else is known yet |

Sent from `fakesangeet@snu.edu.in` (the VM's `SMTP_FROM`). Demo tickets made
with `scripts/demo_ticket.py` queue no email at all.

---

## The email — buyer's version

**Subject:** Your Fake Sangeet ticket — one step left

```text
Hi <name>,

Your ticket for Fake Sangeet is confirmed.

Before the night, sign in and upload a photo of your student ID — that is the
only thing left to do. Your pass appears once it has been checked.

https://fakesangeet.live/pass

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm
```

## The email — partner's version

Sent to the second person on a SELF_CLAIM couple.

**Subject:** You have a seat at Fake Sangeet — one step left

```text
Hi <name>,

Someone has booked a couple ticket for Fake Sangeet and put your name on the
second seat.

Before the night, sign in and upload a photo of your student ID — that is the
only thing left to do. Your pass appears once it has been checked.

https://fakesangeet.live/pass

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm
```

Both are sent as plain text with an HTML copy of the same words; the link is
clickable in the HTML part. `<name>` is filled in at send time; if there is no
name, the greeting is just "Hi,".

**The email never contains the QR code**, on purpose. A QR is the ticket
itself — anyone holding it gets in — and one sitting in an inbox is one forward
away from being somebody else's. The QR only ever appears on the pass page,
behind the holder's own sign-in.

> ### ⚠️ Two things to fix before the send
>
> 1. **The partner's email greets them with the buyer's name.** Both emails on
>    a couple are filled from the order's purchaser name, so the partner reads
>    "Hi Aarav Sharma," on a message meant for them. The 418 queued rows already
>    carry that name, so a fix has to correct those rows too, not just the code.
> 2. **The ID page promises an email that does not exist.** After an upload
>    it says "You'll get an email when it's been looked at", but the only email
>    the system can send is this one. Either add a "your ID was checked" email
>    or change that sentence.

---

## How an attendee gets to their pass

### 1. Open the link and sign in

Go to **https://fakesangeet.live** (or tap the link in the email, which goes
straight to the pass page). Sign in with Google using the **SNU account the
ticket was bought with** — `<netid>@snu.edu.in`. A personal Gmail will not
work; sign-in is limited to `snu.edu.in`, and the ticket is tied to the NetID.

### 2. Upload a student ID

The first visit lands on **Your student ID**:

- Take a photo of the whole card, flat and in good light. A person checks it,
  so it only has to be readable.
- Tick the consent box (the photo is used at the door and deleted 45 days
  after the event).
- **Send for checking.** Three tries are allowed; if the team asks for a
  clearer photo, the page says why and lets them try again.

### 3. Wait for the check

The page shows "Your photo is with the team" until an admin approves it at
`/ops/ids`. There is no email for this step yet (see the warning above), so
attendees should simply come back to the site.

### 4. The pass appears

Once approved, **Your pass** shows a film-strip ticket with:

- the holder's name, Single or Couple, and the seat number;
- **the QR code** the door scans;
- a short code (e.g. `MM2-F4K`) the door can type in if a scan fails;
- a stamp saying where they are: Not arrived, Inside, On a break, Departed.

Tip for attendees: open the browser menu and choose **Add to home screen** so
the pass is one tap away at the door.

### 5. Couples

- **Partner named on the sheet (SELF_CLAIM):** each person has their own
  ticket on their own account. The partner gets the partner email above,
  signs in as themselves and uploads their own ID. Nothing to forward.
- **Partner not named (PROXY):** the buyer sees two passes. The second is
  **locked** — a padlock instead of a QR — with **"Bought an extra ticket for
  your partner? Transfer it to them now."** The buyer types the partner's
  NetID and confirms. The partner then signs in as themselves, uploads their
  own ID and gets their own QR. A partner pass that has not been sent by
  **3pm on 15 October** stays locked, and that seat cannot be used.

### 6. Giving a ticket to someone else

Under the pass: **Give this pass to someone else**. Rules the site enforces:

- the recipient's NetID must be real (two letters, three digits) and they must
  not already hold a ticket;
- **a ticket can be transferred once, ever** — the new holder cannot pass it on;
- the giver's QR stops working immediately, and the new holder uploads their
  own ID before they get a QR;
- **transfers close at 3pm IST on 15 October**, three hours before doors.

On a PROXY couple whose partner pass is unsent, transferring your own ticket
moves both passes; the new holder can still send the second one to their
partner.

### 7. At the door

Show the QR. The volunteer scans it and sees the ID photo that was approved,
and decides — nothing is admitted automatically:

- **first scan:** check in;
- **later scans:** step out for one break, come back in, or check out;
- **one break per pass**, and **check-out is final** for the night.

Anyone stuck at any step — wrong account, no ticket found, ID refused —
should talk to the desk.

---

## Sending it, when the time comes

Not before the phase-2 sheet is in. The order is in the project notes
(`CLAUDE.md`, "Do not start the prod mailer"): real SMTP into the VM's `.env`,
make room for and ingest phase 2, set the cap to what was sold, then start the
`mailer` service. Fix the two issues above first.
