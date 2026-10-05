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

Sent as **`Fake Sangeet <snuphoria@snu.edu.in>`** through Gmail SMTP, with an
app password on the snuphoria account in the VM's `.env` (set up and verified
5 Oct 2026). Replies and bounces land in the snuphoria inbox. Demo tickets made with
`scripts/demo_ticket.py` queue no email at all.

Every email ends with the same footer (`_footer_text` / `_footer_html` in the
templates): the Snuphoria x Inferno Core sign-off and a contact — Pragun
Kathuria, Associate Secretary 1, Snuphoria, 9000000000, pk944@snu.edu.in or
snuphoria@snu.edu.in.

To see all five formats in an inbox without touching the held queue:
`scripts/send_sample_emails.py --to <one address>` sends one of each, marked
[SAMPLE], straight through SMTP.

---

## The email — buyer's version

**Subject:** Your Fake Sangeet ticket — one step left

```text
Hi <name>,

Your ticket for Fake Sangeet is confirmed.

Sign in and upload a photo of your student ID — that is the only thing left to
do. Your pass appears once it has been checked.

Please finish setting up your pass before 13 October.

https://fakesangeet.live/pass

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm

— Snuphoria x Inferno Core

Facing any issues? Reach out to Pragun Kathuria (Associate Secretary 1, Snuphoria)
on 9000000000, or at pk944@snu.edu.in or snuphoria@snu.edu.in.
```

## The email — partner's version

Sent to the second person on a SELF_CLAIM couple.

**Subject:** You have a seat at Fake Sangeet — one step left

```text
Hi <name>,

Someone has booked a couple ticket for Fake Sangeet and put your name on the
second seat.

Sign in and upload a photo of your student ID — that is the only thing left to
do. Your pass appears once it has been checked.

Please finish setting up your pass before 13 October.

https://fakesangeet.live/pass

Sign in with your SNU account. Anyone can open that link, but only you can see
your pass.

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm

— Snuphoria x Inferno Core

Facing any issues? Reach out to Pragun Kathuria (Associate Secretary 1, Snuphoria)
on 9000000000, or at pk944@snu.edu.in or snuphoria@snu.edu.in.
```

The 13 October date is the client's (5 Oct 2026); it is a request in the email,
not something the site enforces — uploads stay open after it. In the HTML copy
it sits in a highlighted "Deadline" panel.

Both are sent as plain text with an HTML copy of the same words. The HTML copy
is a branded page — the scarlet film-strip header with the Fake Sangeet
wordmark, a heading, one button, and the footer — built the way the Primus IQ
daily report is (inline styles, table layout, a phone-width rule), in the
poster's colours. It uses no images, which many inboxes block by default.
`_layout()` in the templates is the one place it lives. `<name>` is filled in at send time; if there is no
name, the greeting is just "Hi,".

**The email never contains the QR code**, on purpose. A QR is the ticket
itself — anyone holding it gets in — and one sitting in an inbox is one forward
away from being somebody else's. The QR only ever appears on the pass page,
behind the holder's own sign-in.

Greetings use each person's own name (fixed 5 Oct 2026). The partner's name
comes from the Phase 1 form's "Partner's Name" column; Early Bird packs a Duo
into one name cell, which is split only when the NetID initials prove who is
who. Anyone the sheet cannot name with certainty gets a plain "Hi," — never
somebody else's name.

---

## The "your ID was checked" email

Sent when an admin decides an ID upload — one per decision, to the address
the person signed in with, greeting them by their Google name. Three versions:

| Decision | Subject | Points to |
|---|---|---|
| Approved | Your ID is approved — your Fake Sangeet pass is ready | `/pass` |
| Asked for a clearer photo | Fake Sangeet — we need a clearer photo of your ID | `/verify`, with the reviewer's note |
| Refused | Fake Sangeet — your ID wasn't accepted | the site, and "talk to the desk", with the note |

Approved, word for word:

```text
Hi <name>,

Your student ID has been checked and approved. Your pass is ready.

Sign in to see it, and keep it handy on your phone for the door:

https://fakesangeet.live/pass

Fake Sangeet — band baaja no baarat
15 October, UAC Terrace, 6pm

— Snuphoria x Inferno Core

Facing any issues? Reach out to Pragun Kathuria (Associate Secretary 1, Snuphoria)
on 9000000000, or at pk944@snu.edu.in or snuphoria@snu.edu.in.
```

Like the ticket email, it never carries the QR. These are queued behind the
same held mailer, so — like everything else — **none go out until the mailer is
started**. Taking a demo back with `reset_identity.py` deletes its review
emails and its uploaded photo too.

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

The page shows "Your photo is with the team" until an admin decides it at
`/ops/ids`; the decision sends the email above (once the mailer is running).

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
`mailer` service.
