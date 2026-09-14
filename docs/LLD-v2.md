# unigate — LLD v2 (Single-Event Dry Run Edition)

**Supersedes:** `unigate-LLD.md` v1 for everything it covers. v1 remains the reference for anything deferred — it is the "if the dry run works" target, not the thing you build now.
**Scope:** one collaboration event. One tenant. You are owner, admin, moderator and on-call.
**Scale target:** 1500 concurrent authenticated users at the sales/registration peak; ~600–800 people through the gate.
**Budget target:** under ~₹3,000 / ~$35 for the whole event month.

---

## 0. What changed from v1, and why

| Dropped / deferred | Reason | Replacement in v2 |
|---|---|---|
| Multi-tenant config, per-event SSO app, subdomain routing | one event | single `EVENT` row, config in `.env` + one `events` row; `event_id` columns **stay** (cheap now, saves a migration later) |
| Row Level Security | one tenant, one DB user | plain `WHERE event_id = …`, still always written |
| Cloud Run × 3, Global LB, Cloud Armor, VPC connector, Memorystore | ~$60–80/mo of that is the load balancer alone | **one e2-medium VM running docker compose**, Cloudflare free in front |
| Cloud SQL HA + read replica | ~$90/mo | Postgres container on the same VM + `pg_dump` to GCS every 30 min + WAL archiving |
| Terraform, GitHub Actions, canary deploys, staging env | you are the only deployer | one `deploy.sh` (git pull → build → compose up) + a `rollback.sh` |
| PgBouncer | one app instance, one pool | direct asyncpg pool, `pool_size=20` |
| Materialised views, Cloud Tasks, Cloud Scheduler | 1 event of data | plain indexed queries; arq worker with cron schedules inside the same compose stack |
| Wallet passes, rotating QR, per-ticket-type break policy, 2-person reject rule | not needed for a dry run | static signed QR + human check |
| VERIFIER as a separate role | you and 2–3 core members do everything | merged into `ADMIN` |
| SECURITY device pre-approval flow | 4–6 known phones | device registers on first login; you see a device list and can kill any session |

Everything **kept** from v1 without change: the four-scan state machine (§11 v1), the QR token format (§10.1 v1), append-only `scans`, idempotency on every mutation, signed-URL direct uploads, and the OCR scoring/decision model. Those are the parts that are painful to retrofit.

---

## 1. Your six questions, answered

### Q1 — Wrong / bad ID image: reject and ask again
Yes, and the loop is explicit. On `RETRY_REQUIRED` the participant stays on the same page, the uploaded image is discarded from view (the row stays in `id_images` for fraud/dedupe, then purges), and they see **one specific, actionable reason** — never "verification failed":

| Detected | Message shown |
|---|---|
| blur below threshold | "The photo is blurry. Hold steady and tap to focus on the card." |
| glare / washed out | "There's glare on the card. Move away from direct light and retry." |
| card not fully in frame | "Part of the card is cut off. Fit all four corners inside the frame." |
| no text readable | "We couldn't read any text. Take the photo straight on, not at an angle." |
| no photo region found | "This doesn't look like an ID card with a photo. Upload the front side." |
| name mismatch | "The name on the card doesn't match your login (Aarav S.). Upload **your own** ID card." |
| NetID mismatch | "The ID number doesn't match your account. If this is your card, click 'Ask a human'." |
| expired card | "This ID looks expired. Upload a current one, or click 'Ask a human'." |

Rules:
- **3 attempts**, then it stops rejecting and goes to `MANUAL_REVIEW` — a genuine student with a faded 3-year-old card must never get hard-locked out by a robot. Attempt 3's screen says so: "We'll have a human check this — you'll get an email within a few hours."
- An **"Ask a human"** button is present from attempt 1. It jumps straight to `MANUAL_REVIEW` with a free-text note.
- Mismatch of name/NetID never auto-rejects permanently; it queues. You approve or reject by hand.
- Client-side quality gate (blur/darkness/size) still blocks the obviously-bad photo *before* it costs you an upload and an OCR call — most retries should happen without a round trip.

### Q2 — One event, four-and-a-bit roles
```
OWNER      → you. everything, including role assignment and destructive actions.
ADMIN      → core team. approve payments, review IDs, scan, dashboards, exports.
             (= v1's MANAGER + VERIFIER merged)
SECURITY   → gate staff. scan only. sees name + photo + state for 90s per scan. nothing else.
PARTICIPANT→ authenticated + holds an approved ticket. sees registration, ID upload, their QR.
VISITOR    → authenticated, no ticket. sees ONLY the "buy a ticket" flow (phase 2). This is
             your "other users" bucket — it's a real role, not an absence of one, because in
             v2 they can self-serve a purchase.
```
Role source: a `staff` table of NetIDs you seed by hand (5–15 rows). Everyone else is `PARTICIPANT` or `VISITOR`, decided at request time by whether an approved ticket exists for their identity.

### Q3 + Q4 — Lean now, big later
Build order in §11 is 6 milestones, not 9, and none of them is infrastructure work. The rule I'd hold you to: **keep the seams, drop the machinery.** Concretely — keep `event_id` on every table, keep the source-adapter interface for ticket ingestion, keep business logic out of route handlers. Those three cost you nothing today and are the difference between "add a second event" being a weekend and being a rewrite. Everything else (Terraform, CI, HA, multi-region) is genuinely deferrable and you should defer it.

### Q5 — Cost
§9 has the full table. Headline: **one e2-medium VM + Cloudflare free + a GCS bucket ≈ $30–33/month**, dropping to ~$8 if you shut the VM down between events. The single biggest cost you avoid is the GCP global load balancer (~$18/mo before traffic) — Cloudflare does that job for free.

### Q6 — Payments: two cohorts, screenshot approval
This is the biggest change in v2 and it's designed in §3–§5. Summary:
- **Cohort A (already paid, offline):** rows ingested from the Sheet arrive with `payment_status = APPROVED` and `phase = 1`. They log in and go straight to ID verification → QR. They also get a "your ticket is ready, log in here" email at ingestion time, before they ever visit.
- **Cohort B (phase 2, on-platform):** a `VISITOR` logs in, sees the ticket page with your static UPI QR + amount, pays outside the platform, then submits **UTR/reference number + payment screenshot**. That creates a `PENDING_APPROVAL` payment. You approve it in the admin console; approval mints the ticket and pushes them into the same ID-verification funnel.
- One funnel after payment. Two ways in, one pipeline — that's what keeps this from becoming two products.

---

## 2. Roles → permissions (v2)

| Capability | OWNER | ADMIN | SECURITY | PARTICIPANT | VISITOR |
|---|:--:|:--:|:--:|:--:|:--:|
| Approve/reject payments | ✅ | ✅ | — | — | — |
| Review ID verifications | ✅ | ✅ | — | — | — |
| Scan QR, record transitions | ✅ | ✅ | ✅ | — | — |
| See participant PII | ✅ | ✅ | scan-scoped 90 s | own | — |
| Manual state override | ✅ | ✅ | with reason (logged) | — | — |
| Revoke / reissue pass | ✅ | ✅ | — | — | — |
| Manage staff roles, phases, ticket inventory | ✅ | — | — | — | — |
| Export CSV | ✅ | ✅ | — | — | — |
| Submit payment proof | — | — | — | — | ✅ |
| Register + upload ID | — | — | — | ✅ | — |
| View own QR | — | — | — | ✅ | — |

Session: opaque cookie, Redis-backed, 8 h participants / 12 h staff. Unchanged from v1 §8.2.

---

## 3. Ticket phases & inventory

```
events.config = {
  "phases": [
    {"id": 1, "name": "Pre-sale (offline)", "self_serve": false, "price": 0,   "source": "sheet"},
    {"id": 2, "name": "Phase 2",            "self_serve": true,  "price": 499, "inventory": 400,
     "opens_at": "...", "closes_at": "...", "upi_vpa": "event@upi",
     "upi_qr_object": "gs://.../upi-qr.png"}
  ],
  "hard_cap": 800
}
```
**Overselling is the failure mode here.** A screenshot-approval flow has a long human latency between "user pays" and "admin approves", so naive inventory counting will let 600 people pay for 400 seats. Design:

1. When a VISITOR opens the purchase page, create a **hold**: `payment_intents` row, status `HOLD`, TTL 20 minutes, counted against inventory.
2. `available = inventory - (approved + pending_approval + active_holds)`. Displayed live, refreshed every 15 s.
3. Submitting proof converts `HOLD → PENDING_APPROVAL` (TTL removed, still counted).
4. Expired holds are swept every minute and released.
5. When `available == 0`, the page shows "Sold out — join waitlist" and refuses new holds. **Never show the UPI QR when a hold could not be granted** — that is the mechanism that stops someone paying for a seat that doesn't exist.
6. If you approve past inventory anyway (you will, for a friend), it's allowed with an explicit "This exceeds remaining inventory. Approve anyway?" confirm, logged.

---

## 4. Payment submission & approval

### 4.1 Participant-side flow
```
VISITOR logs in (Microsoft SSO)
  → GET /buy  → hold granted (20 min countdown visible)
  → page shows: amount, UPI QR image, VPA text (copyable), deep link upi://pay?pa=…&am=…
  → user pays in their own UPI app
  → user submits:  UTR / reference number (required, 12 digits, validated format)
                   screenshot (required, ≤5 MB, direct-to-GCS signed URL)
                   payer name + phone (prefilled from SSO, editable)
  → status page: "Submitted — we usually approve within N hours." (SSE-updated)
  → on approval: ticket minted, email sent, redirect into ID verification
  → on rejection: reason shown, ONE resubmit allowed, then it goes to your inbox
```

### 4.2 Admin review console
Split screen, queue ordered oldest-first, claimable so two admins don't collide:
- Left: the screenshot (zoom, rotate).
- Right: extracted fields, and — the important bit — **auto-flags**:

| Flag | How | Why it matters |
|---|---|---|
| `UTR_DUPLICATE` | exact UTR already submitted by anyone | the #1 fraud: one payment, ten screenshots shared in a group chat |
| `IMAGE_DUPLICATE` | SHA-256 or perceptual-hash match with a prior submission | same, with a re-crop |
| `AMOUNT_MISMATCH` | OCR'd amount ≠ phase price | underpayment |
| `VPA_MISMATCH` | OCR'd payee VPA ≠ your configured VPA | paid someone else / a fake QR |
| `NAME_MISMATCH` | OCR'd payer name vs SSO name (fuzzy) | someone else's screenshot |
| `STALE_TIMESTAMP` | OCR'd payment time outside the phase window | recycled old screenshot |
| `EDITED_IMAGE` | EXIF software tag, or no EXIF at all with a non-screenshot aspect ratio | photoshopped amount |

Run the **same OCR pipeline** on the payment screenshot that you run on ID cards — different extractor module, same engine, same storage, same scoring shape. Cheap reuse: one `verification` concept, `kind ∈ {ID_CARD, PAYMENT_PROOF}`.

Auto-approve is **off** by default. If the dry run shows the flags are reliable you can later enable "auto-approve when UTR unique + amount exact + VPA exact + no other flags", which would clear ~80% of the queue. Design for it, don't ship it.

**Reconciliation, the thing that actually saves you:** let an admin paste/upload the bank or UPI statement CSV. Match `UTR → payment_intent`. Anything approved with no matching bank row is surfaced as `UNRECONCILED` — that's how you find the person who faked a screenshot after the event.

### 4.3 Payment state machine
```
HOLD ──(20 min)──► EXPIRED
  │ submit proof
  ▼
PENDING_APPROVAL ──approve──► APPROVED ──► ticket minted ──► registration funnel
  │                           
  ├──reject(reason)──► REJECTED ──resubmit(1x)──► PENDING_APPROVAL
  │                              └──2nd reject──► CLOSED (manual only)
  └──refund/cancel──► CANCELLED (if ticket already minted, pass revoked + email)
```

---

## 5. Unified lifecycle

```
COHORT A (pre-paid, sheet)            COHORT B (phase 2, on-platform)
─────────────────────────             ──────────────────────────────
sheet sync (every 5 min)              VISITOR logs in
  → order + ticket, payment=APPROVED    → hold → pays via UPI QR
  → "your ticket is ready" email        → submits UTR + screenshot
        │                               → admin approves
        │                               → ticket minted + email
        └───────────────┬───────────────────────┘
                        ▼
              Microsoft SSO login  (identity ←→ ticket binding)
                        ▼
              Registration details form
                        ▼
              ID card upload → OCR → PASS / RETRY (×3) / MANUAL_REVIEW
                        ▼
              Pass minted → QR PNG → email from event mailbox
                        ▼
              Gate: CHECK_IN → (EXIT_BREAK → RE_ENTRY)? → CHECK_OUT
```

Nothing about the gate changes between cohorts. By the time a QR exists, the two paths are indistinguishable — which is exactly the property you want on event day.

---

## 6. Schema delta from v1

Keep all v1 tables except `ticket_sources` (collapse into one row), `scanner_devices` (keep, simplified), and drop nothing else. RLS policies: skip. Add:

```sql
CREATE TYPE payment_state AS ENUM
  ('HOLD','PENDING_APPROVAL','APPROVED','REJECTED','EXPIRED','CANCELLED','CLOSED');

CREATE TABLE payment_intents (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id),
  phase_id        int  NOT NULL,
  user_id         uuid NOT NULL REFERENCES users(id),
  state           payment_state NOT NULL DEFAULT 'HOLD',
  amount_expected numeric(10,2) NOT NULL,
  currency        char(3) NOT NULL DEFAULT 'INR',
  utr             text,                       -- normalised: upper, no spaces
  payer_name      text,
  payer_phone     text,
  proof_object    text,                       -- gcs path of screenshot
  proof_sha256    text,
  proof_phash     text,
  extracted       jsonb,                      -- {amount, vpa, utr, paid_at, payer}
  flags           text[] NOT NULL DEFAULT '{}',
  resubmit_count  int NOT NULL DEFAULT 0,
  reviewed_by     uuid REFERENCES users(id),
  reviewed_at     timestamptz,
  review_note     text,
  reject_reason   text,
  ticket_id       uuid REFERENCES tickets(id),-- set on approval
  reconciled_at   timestamptz,
  hold_expires_at timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
-- one live intent per person; and a UTR can only ever be claimed once
CREATE UNIQUE INDEX pi_one_live_per_user ON payment_intents (event_id, user_id)
  WHERE state IN ('HOLD','PENDING_APPROVAL','APPROVED');
CREATE UNIQUE INDEX pi_utr_unique ON payment_intents (event_id, upper(utr))
  WHERE utr IS NOT NULL AND state IN ('PENDING_APPROVAL','APPROVED');
CREATE INDEX ON payment_intents (event_id, state, created_at);
CREATE INDEX ON payment_intents (event_id, proof_phash);

CREATE TABLE bank_reconciliation (
  id uuid PRIMARY KEY, event_id uuid NOT NULL,
  utr text NOT NULL, amount numeric(10,2), credited_at timestamptz,
  raw jsonb, matched_intent_id uuid REFERENCES payment_intents(id),
  uploaded_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, utr));
```
Modify `tickets`: add `phase_id int NOT NULL DEFAULT 1` and `payment_intent_id uuid NULL`. Modify `verifications`: add `kind text NOT NULL DEFAULT 'ID_CARD'` so payment-proof OCR reuses the table.

**The `pi_utr_unique` partial index is doing real work** — it makes "ten people submit the same UTR" a database error at insert time, not a thing you have to notice at 2 a.m.

---

## 7. API delta

Added:
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/buy` (page) / `GET /api/v1/phases/current` | VISITOR | price, inventory, window |
| POST | `/api/v1/payments/hold` | VISITOR | create/refresh 20-min hold; returns UPI VPA + QR URL + countdown |
| POST | `/api/v1/payments/{id}/proof-url` | VISITOR | signed GCS upload URL for the screenshot |
| POST | `/api/v1/payments/{id}/submit` | VISITOR | `{utr, payer_name, payer_phone, object_key}` → `PENDING_APPROVAL`, enqueues OCR |
| GET | `/api/v1/payments/{id}/status` (+ SSE) | VISITOR | live status |
| GET | `/api/v1/admin/payments?state=PENDING_APPROVAL` | ADMIN | claimable queue |
| POST | `/api/v1/admin/payments/{id}/approve` | ADMIN | mints ticket, queues email; `?force=true` to exceed inventory |
| POST | `/api/v1/admin/payments/{id}/reject` | ADMIN | `{reason, allow_resubmit}` |
| POST | `/api/v1/admin/reconciliation/upload` | OWNER | bank CSV → match by UTR |
| GET | `/api/v1/admin/reconciliation/report` | OWNER | unmatched approvals, unclaimed credits |

Removed from v1: all `/events/*` management CRUD (config is `.env` + one seeded row), `/internal/webhooks/payment/*`, staff CRUD beyond a seed script, exports beyond two fixed CSVs.

New error codes: `PHASE_CLOSED · SOLD_OUT · HOLD_EXPIRED · UTR_ALREADY_USED · PROOF_REQUIRED · RESUBMIT_LIMIT · PAYMENT_PENDING`.

---

## 8. Architecture v2 (one box)

```
Porkbun DNS  →  Cloudflare nameservers (free plan)
                  │  proxied: TLS, HTTP/3, caching, WAF, rate limiting, DDoS
                  ▼
        GCE e2-medium (asia-south1) — Ubuntu 24.04, docker compose
        ┌──────────────────────────────────────────────┐
        │ caddy       :443  TLS from Cloudflare origin  │
        │ web         Next.js (or FastAPI+Jinja, §8.2)  │
        │ api         FastAPI + uvicorn (2 workers)     │
        │ worker      arq: ocr, email, sync, sweeps     │
        │ postgres:16 local volume + 30-min dumps→GCS   │
        │ redis:7     sessions, locks, arq queue        │
        └──────────────────────────────────────────────┘
                  │                         │
        GCS bucket (images, QR, backups)   Cloud Vision OCR
                  │
        Gmail/Workspace SMTP (event mailbox)
```

**Why a VM and not Cloud Run:** at this size Cloud Run itself is nearly free, but the things it needs around it are not — a global LB is ~$18/mo minimum and Cloud SQL's cheapest HA-less instance is ~$9–15/mo, plus a VPC connector. One VM with docker compose is one `ssh` away from debugging, has zero cold starts during your 3-hour gate window, and your `docker-compose.yml` is *identical* to local dev. Trade-off, stated plainly: it's a single point of failure, you patch the OS yourself, and a redeploy is ~20 s of downtime. For a dry run with a paper fallback at the gate, that's the right trade. Take a snapshot of the disk the night before.

**Cloudflare does the heavy lifting for free:** TLS certs, caching all static assets, rate-limiting rules (free tier gives you a few), bot protection, and — the one that matters — it absorbs the traffic spike when your ticket link hits a WhatsApp group with 4,000 people in it. Set Porkbun's nameservers to Cloudflare's, proxy the A record (orange cloud), SSL mode **Full (strict)** with a Cloudflare Origin Certificate installed in Caddy.

### 8.2 One simplification worth considering
For a single event, you can skip Next.js entirely: FastAPI + Jinja2 templates + htmx for the participant pages, and a single React/Vite SPA only for the scanner (which genuinely needs camera + offline state). That removes one container, one build toolchain, one runtime, and the entire SSR-session-forwarding problem. If you want the Next.js skills or plan to go big fast, keep Next. I'd pick htmx for the dry run — but this is a preference, not a correctness issue, and the API contract is identical either way.

### 8.3 Offline scanning — kept, in "lite" form
Not negotiable even in v2, because a wifi drop at the door stops the event. Lite version:
- At shift start the scanner downloads `{pass_id_short, serial, state, name}` for all active passes (~800 rows ≈ 50 KB) into IndexedDB, plus the HMAC verification key (24 h expiry).
- Offline: verify signature locally, check state from cache, queue the transition, show an **amber "PROVISIONAL"** screen so the operator knows to look harder at the face.
- On reconnect: replay by `client_scan_uuid`, server resolves by `occurred_at`, conflicts flagged in a report.
- No cached ID photos (PII on a borrowed phone). Online scans show the photo; offline scans don't.

---

## 9. Cost (asia-south1, one event month)

| Item | Spec | Monthly |
|---|---|---|
| Compute Engine | e2-medium (2 vCPU, 4 GB), 20 GB pd-balanced | ~$27 |
| Static external IP | attached to running VM | ~$3 |
| GCS Standard | ~5 GB (2,300 images + QRs + backups) | ~$0.15 |
| GCS egress | negligible (signed URL reads, Cloudflare caches) | ~$0.50 |
| Cloud Vision OCR | ~2,300 calls (1,500 IDs + 800 payment proofs); first 1,000/mo free, then $1.50/1,000 | ~$2 |
| Cloudflare | Free plan | $0 |
| Email | Workspace mailbox you already have (SMTP, 2,000/day limit) | $0 |
| Domain | Porkbun `.com`-ish, annualised | ~$1 |
| **Total** | | **≈ $33 / ₹2,900** |

Levers if you need it cheaper: **e2-small** (2 GB) handles this load if you cap uvicorn at 2 workers and give Postgres `shared_buffers=384MB` — saves ~$14/mo, at the cost of headroom. Swap Vision for **PaddleOCR in-container** (free, ~2.5 s/image on 2 vCPU, needs a queue and about 1.2 GB RAM — only worth it on e2-medium). Stop the VM between the sales window and event week: you pay only for the disk (~$2/mo).

Costs that would appear if you skipped the design above: GCP global LB $18/mo, Cloud SQL $15–90/mo, Memorystore $35/mo, NAT gateway $32/mo. That's the ~$100/mo you're avoiding.

---

## 10. Capacity: 1,500 concurrent

"1,500 concurrent" during a ticket drop means, realistically: 1,500 people with the page open, maybe 120–200 requests/second in the first 60 seconds, then a long tail.

| Layer | Handles it because |
|---|---|
| Cloudflare | caches HTML shell, CSS, JS, the UPI QR image, the event poster — 80%+ of that spike never reaches your VM |
| Caddy | HTTP/2, static fallthrough, connection reuse |
| FastAPI (2 uvicorn workers × async) | the hot path is `GET /api/v1/phases/current` → **served from Redis with a 10 s TTL**, ~0.3 ms. Never query Postgres for the inventory counter on the hot path. |
| Hold creation | the only write in the spike. ~400 inserts total (inventory-bounded), single-row, with a Redis token bucket at 1 req/5 s per user |
| Screenshot uploads | direct to GCS, zero VM bandwidth |
| Postgres | peak is a few hundred writes/min. A local Postgres on 2 vCPU does thousands/sec. Not the bottleneck. |

Load-test before the drop (`k6`, 1,500 VUs, 3-minute ramp) and watch three numbers: p95 on `/phases/current`, Postgres connection count, and VM memory. Tune `pool_size` down if connections climb.

**The actual bottleneck is you.** 800 payment screenshots × 20 seconds of human review = 4.5 hours of admin time. Plan for 3 people on the queue for the first evening, set participant expectations to "within 12 hours" in the UI, and prioritise the queue by submission time so nobody waits two days.

---

## 11. Milestones (dry-run edition)

| # | Milestone | Contents | Done when |
|---|---|---|---|
| M1 | Skeleton + auth | compose stack, settings, Postgres/Alembic, Microsoft SSO, sessions, roles seeded, `/me` | you log in as OWNER, a test NetID logs in as VISITOR |
| M2 | Access core | tickets/passes/scans models, state machine, token mint/verify, `/scans` + `/scans/confirm`, exhaustive transition tests | 4-scan lifecycle passes in tests and in curl |
| M3 | Sheet ingestion + cohort A | one Sheet adapter, column mapping, idempotent sync, "your ticket is ready" email | a 200-row sheet syncs twice, zero duplicates, emails land in inbox not spam |
| M4 | Registration + ID OCR | binding, details form, signed-URL upload, OCR, scoring, the retry loop from §1, review queue | 30 real ID photos scored; retry messages verified by hand |
| M5 | Payments (cohort B) | holds + inventory, UPI QR page, proof submission, OCR flags, admin approve/reject, reconciliation CSV | a friend buys a ticket end-to-end; duplicate UTR is blocked |
| M6 | Pass + scanner | QR render, confirmation email, scanner PWA, offline lite, dashboard, paper fallback export | **20-person dress rehearsal at a real door, including one wifi-off scan** |

M6's dress rehearsal is the milestone that matters. Everything before it is provably correct; only the rehearsal tells you whether a tired volunteer at 9 p.m. can use it.

---

## 12. Ops-lite runbook

- **Deploy:** `./deploy.sh` = `git pull && docker compose build api worker web && docker compose up -d && docker compose exec api alembic upgrade head`. ~20 s. Don't deploy on event day after 6 a.m.
- **Rollback:** `git checkout <prev-tag> && ./deploy.sh`. Keep migrations backward-compatible for one release so this actually works.
- **Backups:** cron `pg_dump | gzip | gsutil cp` every 30 min to `gs://…/backups/`, 14-day lifecycle. **Test the restore once, before the event** — an untested backup is a rumour.
- **Monitoring:** Cloudflare analytics + a free UptimeRobot check on `/healthz` (5 min, SMS to you) + `docker compose logs` shipped nowhere. Sentry free tier for exceptions is worth the 10 minutes it takes to add.
- **Event-day checklist:** disk snapshot taken · backup restore tested · Entra client secret expiry > 30 days away · 3 charged scanner phones + 2 power banks · printed pass list sorted by short code · your phone number on the support page · paper CSV import path rehearsed.

---

## 13. Security floor (do not cut below this)

Even for a dry run: HTTPS only (Cloudflare Full strict); `HttpOnly; Secure; SameSite=Lax` session cookies + CSRF tokens; deny-by-default role dependency on every route; signed URLs only for images (bucket private, no public objects — a payment screenshot contains someone's bank details and their phone number); no PII in logs; audit rows for every payment approval, ID decision, override and export; ID images and payment screenshots purged at **T+45 days** by a cron; rate limits on auth, OTP, upload-URL minting and scans. The consent checkbox and privacy notice still ship — you're collecting ID cards and bank screenshots from ~1,500 students, and DPDP applies to a student club exactly as it applies to a company.

---

## 14. `CLAUDE.md` v2 seed

Replace the §22.2 block from v1 with this:

```markdown
# CLAUDE.md — unigate (single-event dry run)

## Read order
1. `docs/LLD-v2.md` — authoritative. §1 (retry loop), §4 (payments), §6 (schema), §7 (API)
   are binding. `docs/LLD-v1.md` is the future-state reference ONLY; do not build from it.
2. This file.

## Project shape — mirror the Primus IQ structure
Reuse the layering, tooling and conventions of the Primus IQ platform. Where this doc is
silent, infer from Primus IQ, not from generic tutorials:
- **Thin routes, fat services.** `api/v1/routes/*` validate → authorize → call a service →
  shape a response. All domain logic in `services/`. Services never import FastAPI.
- **Async everywhere.** SQLAlchemy 2.0 async + asyncpg, `httpx.AsyncClient`, arq worker.
  Blocking CPU work goes to the worker or a threadpool, never inline in a request.
- **Pydantic at every boundary** — settings, request/response, job payloads, `events.config`.
- **Alembic for every schema change**, hand-edited after autogenerate, expand/contract only.
- **`docker compose up` is the whole dev environment** — local postgres, redis, mailpit,
  fake-gcs. No cloud credentials required to run or test. `uv` for Python deps.
- **Enforce with tooling, not prose.** When a rule matters, a constraint or a dependency
  enforces it: partial unique indexes (`pi_utr_unique`, one-ticket-per-identity), the
  server-issued `decision_token`, append-only `scans`. Not a comment asking nicely.
- **Derive gates dynamically** — never hardcode a "known values" set in a validator; compute
  it from the source of truth at import time.
- **SSE for progress streams**, with the reconnect/backoff pattern from the Primus IQ review
  panel; DOMPurify any server-provided HTML.
- **Cloud SQL Auth Proxy gotcha** (if you ever point at a cloud DB): the instance connection
  name is not a TCP host; use `127.0.0.1:5432` and pass `ssl=disable` to asyncpg, in settings
  construction, not in ad-hoc scripts.

## Scope discipline — this is the dry run
DO NOT introduce: Terraform, CI pipelines, Kubernetes, multi-tenancy plumbing, RLS,
PgBouncer, materialised views, Cloud Tasks, staging environments, or a second cloud service.
DO keep the seams that make v1 possible later: `event_id` on every table, the
`TicketSource` adapter interface, and business logic out of routes.
If a task seems to need one of the forbidden items, stop and say so instead of adding it.

## Deliverable conventions
- Complete, drop-in files. No diffs, no fragments, no "...rest unchanged".
- Surgical edits: change what was asked, don't reformat or reorganise around it.
- Diagnose before implementing; show the evidence and confirm the root cause first.
- Two-line summaries by default.

## Guardrails
- Never log PII; hash emails, NetIDs and QR tokens.
- Never f-string SQL.
- Payment screenshots and ID images: private bucket, signed URLs only, never emailed,
  never cached on a scanner device, purged at T+45d.
- SECURITY-role responses carry only the fields listed in LLD-v1 §11.5.
- Scan mutations are always: append to `scans` + guarded `UPDATE passes` in one transaction.
- Approving a payment must be idempotent and must write an audit row.

## Build order
LLD-v2 §11, M1 → M6. Do not start a milestone before the previous one's tests pass.
```

---

## 15. Still open

1. **Phase-2 price and inventory** — needed to seed `events.config`.
2. **UPI destination** — personal VPA, club account, or college account? A personal VPA receiving ~₹2 lakh will get flagged by the bank; a current account is worth the paperwork.
3. **Refunds** — do you offer any? If yes, define the window now; `CANCELLED` + pass revocation is already in the schema, but the policy text isn't.
4. **Approval SLA** — what do you promise on the status page? Pick a number you can hit with 3 admins.
5. **Non-university attendees for this specific event** — if the collab brings students from another college, they have no NetID in your tenant, and the SSO-only path locks them out entirely. This is the single most likely event-day surprise. Decide now: email-OTP fallback, or an admin-created guest ticket with manual ID review?
6. **ID card policy** — which cards count, and what's the override for someone who lost theirs?
