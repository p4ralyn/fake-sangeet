# University Event Ticketing, Registration & Access Control Platform — Low Level Design

**Codename:** `unigate` (working name — one platform, many events)
**Version:** LLD v1.0
**Stack:** FastAPI (async) · PostgreSQL 16 · Redis · Next.js 14 (App Router) · Docker · Google Cloud Run / Cloud SQL / GCS · Microsoft Entra ID (SSO) · Google Cloud Vision OCR
**Scale target:** 600 concurrent users per event, ~5–10 parallel live events, gate throughput 40 scans/min/gate

---

## 0. How to read this document

Sections 1–6 are product and flow definition. Sections 7–14 are the contract-level design (schema, APIs, state machines, token formats) — these are the parts an implementing agent must follow literally. Sections 15–21 are infrastructure, security and operations. Section 22 is the repo layout and the **agent bootstrap block** (`CLAUDE.md` seed) that tells a local Opus/Claude Code session to mirror the Primus IQ project conventions. Section 23 lists decisions still open for you.

---

## 1. Scope

### 1.1 In scope
- One multi-tenant platform serving **all** university events; each event is a tenant with its own branding, its own Microsoft SSO app registration, its own sender mailbox, its own staff roster.
- Ingest ticket-purchase / registration-intent data from Google Form → Sheets responses (plus a generic adapter for payment-gateway webhooks and CSV upload).
- Gate the registration flow so **only confirmed ticket holders** ever see it.
- Microsoft SSO login, binding the authenticated NetID to a ticket.
- ID-card image upload → OCR → automated verification → manual review fallback.
- Issue a signed QR pass, delivered by email from the event's own mailbox.
- Security/RBAC scanner app: scan QR → show participant record + stored ID image → human confirm → state transition.
- Four-scan lifecycle per pass: `CHECK_IN → EXIT_BREAK → RE_ENTRY → CHECK_OUT`, with the operator choosing between "exit for break" and "final check-out" when a participant leaves.
- Live dashboards: occupancy, verification funnel, gate throughput, exception queue.

### 1.2 Out of scope (v1)
- Payment collection inside the platform (we consume proof of purchase; we do not process cards).
- Seat/zone allocation, printed badge design, catering/merch redemption.
- Face recognition / biometric matching. Identity confirmation is **human-in-the-loop** against the stored ID image. (Left as a documented extension point, not shipped — legal review needed.)
- Native mobile apps. The scanner is an installable PWA.

### 1.3 Non-functional targets
| Metric | Target |
|---|---|
| Scan API p95 (server) | ≤ 150 ms |
| Scan API p99 | ≤ 400 ms |
| Registration page TTFB | ≤ 600 ms |
| OCR decision latency (async) | p95 ≤ 20 s |
| Email delivery (queued → sent) | p95 ≤ 60 s |
| Availability during event window | 99.9% |
| Concurrent authenticated sessions | 600 sustained, 1500 burst |
| Data loss tolerance (RPO) | 5 min (PITR) |
| Gate downtime tolerance | 0 — scanner must work offline |

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Event** | A tenant. Owns config, staff, tickets, mail identity, SSO app registration. |
| **NetID** | The university Microsoft account identifier (e.g. `f20230123@university.edu`), extracted from the `preferred_username` / `upn` claim. |
| **Order** | A row ingested from the ticket source (Sheet row / gateway webhook). Proof of purchase. |
| **Ticket** | A single admission right derived from an order. `1 order → N tickets` (group bookings). |
| **Registration** | The binding of a ticket to an authenticated identity + a verified ID card. |
| **Pass** | The issued credential: a QR-encoded signed token + its server-side state. |
| **Scan** | An immutable append-only event recording a gate action. |
| **Occupancy** | Count of passes currently in state `INSIDE`. |

---

## 3. Actors & RBAC

### 3.1 Roles
| Role | Scope | Granted to |
|---|---|---|
| `PLATFORM_ADMIN` | global | You / IT |
| `EVENT_OWNER` | one event | Club president / organiser lead |
| `EVENT_MANAGER` | one event | Core team; configures event, runs reports |
| `VERIFIER` | one event | Reviews OCR-failed ID cards manually |
| `SECURITY` | one event + one or more gates | Gate staff who scan |
| `PARTICIPANT` | own ticket only | Attendee |

### 3.2 Permission matrix
| Capability | ADMIN | OWNER | MANAGER | VERIFIER | SECURITY | PARTICIPANT |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Create/archive events | ✅ | — | — | — | — | — |
| Edit event config, branding, gates | ✅ | ✅ | ✅ | — | — | — |
| Assign staff roles | ✅ | ✅ | ⚠️ (≤ own level) | — | — | — |
| Trigger/inspect sheet sync | ✅ | ✅ | ✅ | — | — | — |
| View participant PII (full) | ✅ | ✅ | ✅ | ✅ | ⚠️ scan-scoped, 90 s TTL | own only |
| View stored ID image | ✅ | ✅ | ⚠️ audit-logged | ✅ | ⚠️ scan-scoped | own only |
| Approve/reject verification | ✅ | ✅ | ✅ | ✅ | — | — |
| Scan QR / record transitions | ✅ | ✅ | ✅ | — | ✅ | — |
| Force-override state (manual) | ✅ | ✅ | ✅ | — | ⚠️ with reason + supervisor PIN | — |
| Revoke / reissue pass | ✅ | ✅ | ✅ | — | — | — |
| Export CSV (PII) | ✅ | ✅ | ⚠️ watermarked | — | — | — |
| Read audit log | ✅ | ✅ | read-only | — | — | — |

**Staff identity:** all staff authenticate with the *same* Microsoft SSO tenant. A staff member is a row in `event_staff(event_id, net_id, role, gate_ids[])`. `SECURITY` accounts are ordinary university NetIDs — no shared passwords, no service accounts at the gate. This satisfies "security RBAC under the NetIDs of the event managers".

**Device binding (gap you didn't cover):** a `SECURITY` session must be bound to a registered `scanner_device` (device UUID persisted in PWA storage, approved once by a MANAGER). Prevents a leaked session from being used to check people in from off-site.

---

## 4. System architecture

```
                  Porkbun DNS  ──►  Google Cloud DNS (NS delegation)
                                        │
                        ┌───────────────┴───────────────┐
                        │  Global External HTTPS LB     │
                        │  + Cloud Armor (WAF, rate)    │
                        │  + Managed TLS (apex + wild)  │
                        └───────────────┬───────────────┘
            ┌───────────────────────────┼───────────────────────────┐
            ▼                           ▼                           ▼
   Cloud Run: web (Next.js)   Cloud Run: api (FastAPI)   Cloud Run: worker (arq)
   - SSR registration UI      - REST + SSE               - sheet sync
   - scanner PWA              - authz, state machine     - OCR pipeline
   - admin console            - QR mint/verify           - email dispatch
            │                           │                           │
            └──────────┬────────────────┼───────────────┬───────────┘
                       ▼                ▼               ▼
              Memorystore Redis   Cloud SQL Postgres   GCS buckets
              (cache, locks,      (primary + PITR,     (id-images, qr-png,
               rate-limit, queue)  read replica)        exports, audit-cold)
                       │                                      │
                       └──────────────► Secret Manager ◄──────┘

  External: Microsoft Entra ID (OIDC) · Google Sheets API · Cloud Vision OCR
            · Email provider (Workspace SMTP relay / SendGrid) · Cloud Tasks + Scheduler
```

### 4.1 Service boundaries
- **`api`** — stateless FastAPI. Owns all writes. Never calls OCR or SMTP inline; it enqueues.
- **`worker`** — arq (Redis-backed async job runner; same codebase, different entrypoint). Handles sheet sync, OCR, email, QR PNG rendering, reconciliation, retention purges.
- **`web`** — Next.js. Server components for gated pages, client components for the scanner.
- **Why not Celery:** the codebase is fully async (asyncpg/SQLAlchemy async). arq is asyncio-native, Redis-only, ~200 LOC of integration. Celery would force a sync bridge.

### 4.2 Domain routing
| Host | Serves |
|---|---|
| `events.<yourdomain>` | landing + event directory |
| `<event-slug>.<yourdomain>` | participant registration flow (tenant resolved from subdomain) |
| `scan.<yourdomain>` | scanner PWA (device-bound) |
| `admin.<yourdomain>` | manager/verifier console |
| `api.<yourdomain>` | FastAPI |

Porkbun: set nameservers to Cloud DNS, then A/AAAA records for apex + `*` wildcard pointing at the LB anycast IP. Wildcard managed certificate requires DNS authorization (Cloud DNS makes this one-click). Keep TTL at 300 during launch week.

---

## 5. End-to-end lifecycle

```
[1] Ticket bought (external)  →  Google Form response row / payment webhook
                                          │  sync job (every 2 min + manual trigger)
                                          ▼
[2] order + ticket rows created, status=PURCHASED
                                          │  "registration open" email w/ magic link
                                          ▼
[3] Participant opens https://<event>.domain/r/<ticket_ref>
                                          │  Microsoft SSO (OIDC + PKCE)
                                          ▼
[4] Identity binding: NetID ←→ ticket   (auto-match, else claim flow)
                                          │
                                          ▼
[5] Registration form + ID card upload (client-side quality gate)
                                          │  async OCR job
                                          ▼
[6] Verification decision: AUTO_PASS / MANUAL_REVIEW / AUTO_FAIL(retry)
                                          │
                                          ▼
[7] Pass minted → QR PNG → confirmation email from event mailbox
                                          │
                                          ▼
[8] Gate: scan QR → operator sees record + ID image → confirm →
      CHECK_IN → (EXIT_BREAK → RE_ENTRY)? → CHECK_OUT
                                          │
                                          ▼
[9] Post-event: occupancy reconciliation, auto-close, retention purge (T+30d)
```

### 5.1 Ticket → registration state machine
```
PURCHASED ──claim──► CLAIMED ──submit──► PENDING_OCR ──┬─► VERIFIED ──mint──► PASS_ISSUED
    │                   │                              ├─► MANUAL_REVIEW ─┬─► VERIFIED
    │                   │                              │                  └─► REJECTED
    │                   │                              └─► RETRY_REQUIRED ──► PENDING_OCR
    │                   └──timeout(T-24h)──► EXPIRED_UNCLAIMED
    └──refund/cancel──► CANCELLED        (any state) ──admin──► REVOKED
```
Invariants:
- A ticket may be bound to **exactly one** NetID (`UNIQUE(event_id, net_id) WHERE status NOT IN (CANCELLED, REVOKED)`).
- `PASS_ISSUED` is terminal-forward: further ID uploads are rejected unless an admin resets to `PENDING_OCR` (which auto-revokes the old pass).
- `RETRY_REQUIRED` allows `max_id_upload_attempts` (default 3) before forcing `MANUAL_REVIEW`. Never allow infinite OCR retries — it is both a cost and a fraud-probing vector.

---

## 6. Multi-tenancy model

**Isolation strategy:** single database, shared schema, `event_id` on every domain table, enforced by **PostgreSQL Row Level Security** plus an application-level tenant guard. Belt and braces: RLS catches the query someone forgot to filter.

```sql
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tickets
  USING (event_id = current_setting('app.event_id', true)::uuid);
```
The API sets `SET LOCAL app.event_id = $1` at the start of every request transaction (a FastAPI dependency wrapping the session). `PLATFORM_ADMIN` uses a separate role with `BYPASSRLS`.

**Per-event configuration** lives in `events.config` (JSONB, validated by a Pydantic `EventConfig` model — never free-form):
```jsonc
{
  "sso": { "tenant_id": "...", "client_id": "...", "allowed_domains": ["university.edu"],
           "client_secret_ref": "projects/x/secrets/evt-abc-sso/versions/latest" },
  "mail": { "from_name": "Techfest 2026", "from_address": "techfest@university.edu",
            "reply_to": "techfest@university.edu", "provider": "workspace_dwd" },
  "verification": { "require_id_card": true, "ocr_min_confidence": 0.82,
                    "name_match_threshold": 0.86, "max_id_upload_attempts": 3,
                    "accepted_id_types": ["STUDENT_ID", "STAFF_ID"] },
  "access": { "allow_reentry": true, "max_breaks": 1, "reentry_window_minutes": 180,
              "checkin_opens_at": "...", "checkout_auto_at": "..." },
  "branding": { "logo_url": "...", "primary": "#0B5FFF", "support_email": "..." },
  "capacity": { "hard_cap": 600, "warn_at_pct": 90 }
}
```

**Per-event SSO:** you said each event gets its own Microsoft SSO app registration inside the university tenant. Design consequence:
- The OIDC redirect URI is **shared** (`https://api.<domain>/auth/microsoft/callback`) and the event is carried in the signed `state` parameter. Registering N redirect URIs per event is unmanageable; one URI + state-carried tenant is standard and safe (state is an HMAC'd, single-use, Redis-stored nonce with 10-min TTL).
- Client secrets are **never** in `events.config` — only a Secret Manager resource name. The app resolves secrets at request time with a 5-minute in-process cache.
- Add a **secret expiry monitor**: Entra client secrets expire (6/12/24 mo). A daily job alerts 30/7/1 days before expiry. This kills an entire class of "event day, nobody can log in" incidents.

---

## 7. Ticket ingestion (Google Sheets + adapters)

### 7.1 Why an adapter layer
You described Google Form → Sheet. That will not stay true (some events sell through Razorpay/Instamojo, some through the student-affairs portal, some hand out comp tickets). Define one port:

```python
class TicketSource(Protocol):
    source_type: Literal["google_sheet", "payment_webhook", "csv_upload", "manual"]
    async def fetch(self, since: datetime | None) -> AsyncIterator[RawOrder]: ...
```
`RawOrder` is normalised to: `external_id, purchaser_email, purchaser_name, net_id_hint, phone, quantity, ticket_type, amount_paid, currency, purchased_at, payment_ref, raw_payload(JSONB)`.

### 7.2 Google Sheets sync job
- Auth: a **service account** with domain-wide delegation, or simpler — the organiser shares the Sheet with the service account email (viewer). Prefer the latter; no DWD approval needed from IT.
- API: `spreadsheets.values.get` on a named range, not `A:Z`. Store `sheet_id`, `tab_name`, and a **column mapping** (`column_map` JSONB) configured once in the admin console via a "preview + map columns" wizard. Never infer columns by position — organisers reorder them mid-event without telling anyone.
- Cadence: Cloud Scheduler → `/internal/jobs/sync-orders` every 2 min during the open window, plus a manual "Sync now" button. Incremental via `Timestamp` column watermark, with a nightly full reconcile pass.
- **Idempotency:** `external_id = sha256(sheet_id + response_row_uuid or timestamp+email)`. `INSERT ... ON CONFLICT (event_id, external_id) DO UPDATE` only for mutable fields (name, phone, quantity if not yet claimed). Never resurrect a `CANCELLED` ticket via sync.
- **Row-level errors don't fail the batch.** Each bad row lands in `ingestion_errors` with the raw payload and a human-readable reason, surfaced in an admin "Needs attention" tray.

### 7.3 Gaps this fills (things your spec didn't state)
1. **Paid vs unpaid.** A Form response is *intent*, not *purchase*. Add `payment_status` to `RawOrder`; only `PAID` (or `COMP`) orders become tickets. Where payment lives in the Sheet as a manually-marked column, support that; where it's a gateway, take the webhook as the source of truth and treat the Sheet as descriptive metadata only.
2. **Group bookings.** `quantity > 1` mints N tickets sharing one order. Ticket #1 auto-assigns to the purchaser; #2..N get `claim_code`s the purchaser can forward. Each claimant does their own SSO + ID verification.
3. **Non-university attendees.** Alumni, external speakers, and guests have no NetID. Without a fallback, your gate stops them at step 3. Fallback path: `auth_method = EMAIL_OTP` — 6-digit code to the purchase email, rate-limited, and the ticket is force-routed to `MANUAL_REVIEW` with any government/college ID accepted. `EventConfig.allow_external_attendees` defaults false.
4. **Duplicate / transferred tickets.** Ticket transfer = release claim (admin or purchaser action within a window) → new `claim_code` → old registration revoked, pass invalidated, audit row written.
5. **Cancellations and refunds.** Sync must mark `CANCELLED` when the source says so; if the pass was already issued, revoke it and email the holder.

---

## 8. Authentication & session

### 8.1 Participant login (Microsoft Entra ID, OIDC Authorization Code + PKCE)
1. `GET /auth/microsoft/start?event=<slug>&next=/r/<ticket_ref>` → generate `code_verifier`, `nonce`, `state`; store `{event_id, next, verifier, nonce}` in Redis under `oidc:state:<state>` TTL 600 s; 302 to Entra `/authorize` with `scope=openid profile email User.Read`, `prompt=select_account`, `domain_hint=<university.edu>`.
2. Callback validates `state` (single-use — `GETDEL`), exchanges the code, **validates the ID token properly**: signature via cached JWKS (`https://login.microsoftonline.com/{tid}/discovery/v2.0/keys`, refresh on `kid` miss, 12 h cache), `iss`, `aud == client_id`, `exp/nbf` with 120 s clock skew, `nonce`, and `tid == configured tenant_id`.
3. Extract identity:
   - `net_id` ← `preferred_username` (lowercased, trimmed), fallback `upn`, fallback `email`.
   - `oid` ← immutable object ID. **Store `oid` as the real identity key**; `preferred_username` can change when a student's name or roll format changes. `net_id` is for display and matching.
   - Reject if the domain isn't in `allowed_domains`.
4. We do **not** keep Microsoft access/refresh tokens. No Graph calls at runtime. Session is our own.

### 8.2 Sessions
- Opaque session ID (32 bytes, `secrets.token_urlsafe`) in an `HttpOnly; Secure; SameSite=Lax; Path=/` cookie scoped to the apex domain. Server-side session record in Redis (`sess:<sid>` → `{user_id, event_id, roles, device_id, mfa_ok, ip_hash, ua_hash}`), TTL 8 h sliding (participants) / 12 h fixed (security, matching a shift).
- Why opaque, not JWT: you need instant revocation (staff removed mid-event, stolen scanner). A JWT you can't revoke at a gate is a liability.
- CSRF: double-submit token for all state-changing requests; `SameSite=Lax` alone is insufficient for the scanner's POSTs from a PWA.
- Session pinning: soft-bind to `ip_hash /24` and `ua_hash`. Mismatch → re-auth, not hard block (campus wifi roams).

### 8.3 Identity binding to a ticket ("who are you, and is this your ticket?")
On first authenticated visit to `/r/<ticket_ref>`:
| Case | Rule |
|---|---|
| `ticket.net_id_hint` == authed `net_id` | auto-bind |
| `ticket.purchaser_email` == authed email | auto-bind |
| Ticket unclaimed + valid `claim_code` in URL | bind |
| Ticket already bound to another `oid` | **hard reject**, show support contact, log `BINDING_CONFLICT` |
| NetID has another ticket in this event | reject second binding (one pass per person), unless `allow_multiple_tickets` |
| No matching ticket at all | show "no ticket found" page + a **claim by email** form that searches by the authed email only |

This is exactly the mechanism that makes *"only people who have bought tickets see the registration option"* true: the registration route is a server-side gated route. There is no client-side hiding — the page 404s (not 403 — don't leak ticket existence) unless the session's `oid`/`net_id`/`claim_code` resolves to an eligible ticket.

---

## 9. ID card capture, OCR & verification

### 9.1 Client-side capture gate (before upload)
Cheapest possible filter. Runs in the browser, blocks the upload button:
- min resolution 1000×600 after EXIF orientation fix; max 8 MB; formats `jpeg/png/heic→jpeg`
- blur score via variance-of-Laplacian on a downscaled canvas — reject below threshold
- mean luminance bounds (too dark / blown out), and a glare check (fraction of pixels > 250)
- edge/quad detection hint: "fit the card inside the frame"
- strip EXIF GPS on the client **and** re-strip on the server (never trust the client)
- downscale to max 2000 px long edge before upload — keeps 600 concurrent uploads from saturating egress

### 9.2 Upload path
Direct-to-GCS via **V4 signed resumable URL** issued by the API (`POST /api/v1/registrations/{id}/id-image/upload-url`), 10-min TTL, `Content-Type` and `x-goog-content-length-range` enforced in the signature. The API never proxies image bytes — that's what would melt Cloud Run at 600 concurrent uploads.
- Bucket `unigate-id-images`, uniform bucket-level access, **no public access**, CMEK optional, object path `evt/{event_id}/reg/{registration_id}/{uuid}.jpg`, object lifecycle delete at 45 days.
- On upload completion the client calls `POST .../id-image/complete`; the API verifies the object exists, size/type, computes SHA-256 + pHash, then enqueues `ocr_verify_job`.

### 9.3 OCR pipeline (worker)
```
download → normalise (EXIF, deskew, autocrop quad, grayscale, CLAHE, adaptive threshold)
        → OCR primary: Cloud Vision DOCUMENT_TEXT_DETECTION (full text + per-block confidence)
        → fallback: PaddleOCR in-container if Vision errors/quota (degraded, flag it)
        → field extraction (regex + layout heuristics + fuzzy label anchoring)
        → scoring → decision
```
**Field extraction targets:** `net_id / roll number`, `full name`, `institution name`, `validity/expiry year`, `photo region present?`, `department/programme`.
- NetID/roll regex is **per-event configurable** (`id_patterns: ["^[A-Z]\\d{8}$", ...]`) because every university encodes roll numbers differently. Ship a default + a tester in the admin UI.
- Institution match: fuzzy contains against `event.config.institution_aliases`.
- Photo presence: simple face-region detector (OpenCV Haar/DNN) — not recognition, just "is there a portrait region", to reject screenshots of text and photocopies of timetables.

**Scoring** (weights configurable, sum to 1.0):
| Signal | Weight | Method |
|---|---|---|
| Name match vs authed profile name | 0.35 | normalised (lowercase, strip honorifics/initials/diacritics) token-set ratio (RapidFuzz) |
| NetID/roll match vs authed NetID | 0.35 | exact after normalisation; partial credit on Levenshtein ≤ 1 (OCR confuses `0/O`, `1/I/l`, `5/S`, `8/B`) |
| Institution match | 0.15 | fuzzy contains |
| Image quality + photo present | 0.10 | composite of blur/glare/face-region |
| Card validity year | 0.05 | ≥ current academic year if parseable |

**Decision:**
```
score ≥ 0.85 AND netid_exact AND no_fraud_flag        → AUTO_PASS
0.60 ≤ score < 0.85  OR netid_partial                 → MANUAL_REVIEW
score < 0.60 AND attempts < max                       → RETRY_REQUIRED (guided error message)
score < 0.60 AND attempts ≥ max                       → MANUAL_REVIEW
any fraud flag                                        → MANUAL_REVIEW (never auto-fail — accusing is worse than queuing)
```
Store the full OCR payload (`ocr_raw` JSONB), extracted fields, per-signal subscores and the decision. You will need this to tune thresholds after event #1, and to defend a rejection when a student complains.

**Fraud flags (gap):** pHash collision with another registration's image (same card uploaded twice), identical SHA-256, screenshot detection (uniform background + UI chrome), image from a screen (moiré heuristic), EXIF software = editor, upload IP shared by >N registrations in 10 min.

### 9.4 Manual review console
Queue ordered by `submitted_at`, filterable by reason. Split-screen: ID image (zoom/rotate, no download) on the left, extracted vs authed fields diffed on the right, highlighting mismatches. Actions: **Approve**, **Reject with reason** (templated: blurry / wrong person / expired / not a valid ID / suspected duplicate), **Request re-upload**. Every action writes an audit row with the reviewer's NetID. Two-person rule for `Reject` is configurable.

**Reviewer throughput budget:** at 600 registrations and a realistic 15% manual rate, that's ~90 reviews. At 20 s each, one reviewer clears it in 30 minutes. Fine — but the queue must be **claimable** (`FOR UPDATE SKIP LOCKED` lease with a 3-min TTL) so two reviewers never work the same item.
---

## 10. Pass issuance & QR token design

### 10.1 What goes in the QR
A **compact signed token**, not a URL to a lookup and not raw PII.

```
UG1.<base64url(payload)>.<base64url(sig)>
```
`payload` is CBOR/msgpack-encoded (smaller than JSON → lower QR density → faster scan in bad light):
```python
{ "v": 1, "e": <event_id bytes16>, "p": <pass_id bytes16>, "s": <serial uint16>, "x": <exp uint32> }
```
`sig` = HMAC-SHA256 truncated to 128 bits, keyed per event (`pass_signing_key`, 32 bytes, in Secret Manager, rotatable with `kid` in the first payload byte). Total ≈ 78 chars → QR version 5, ECC level **M**, 4-module quiet zone, min 3 cm printed / 200 px on screen.

**Why HMAC not Ed25519/JWT:** verification happens offline on a phone with no network. HMAC needs a shared key on the device, which is a risk — so the offline key is a *distinct, gate-scoped, event-scoped, 24h-expiring* verification key delivered to an approved device, and offline mode only ever grants **provisional** admission (§12.4). Online verification is always authoritative.

**Why `serial`:** lets you invalidate a reissued pass. Server stores `passes.serial`; a token whose serial ≠ current serial is rejected as `SUPERSEDED`. That's how transfers, re-issues after a lost email, and revocations work without changing `pass_id`.

**Rotation option (documented, off by default):** TOTP-style rotating QR in the participant's web wallet (`/my/pass`, refreshes every 30 s, 90 s validity window). Defeats screenshot-sharing. Cost: attendee needs network at the gate, and the emailed static QR must then be a fallback. Recommendation: **static QR + human face-vs-ID check at the gate** for v1 — your design already has a human in the loop, which is the stronger control.

### 10.2 QR rendering
- `segno` (pure Python, no C deps) → PNG at 600×600 with quiet zone, event logo optionally centred (≤ 18% area, ECC M tolerates it), plus `pass_id` short code in human-readable text underneath for manual fallback.
- Rendered once by the worker, stored at `gs://unigate-qr/evt/{event_id}/{pass_id}-{serial}.png`, attached to email as `cid:` inline **and** as a `.png` attachment (some mail clients block inline images).
- Also generate an **Apple/Google Wallet pass** — optional extension, `PKPass` for iOS. Flagged as post-v1.

### 10.3 Confirmation email
Sent from the event's own mailbox. Two supported providers:

| Provider | How | When to use |
|---|---|---|
| `workspace_dwd` | Google Workspace service account with domain-wide delegation, impersonating `techfest@university.edu` via Gmail API `users.messages.send` | University runs Workspace and IT will grant DWD |
| `smtp_relay` | SMTP relay with per-event credentials | IT gives you a mailbox password, no DWD |
| `sendgrid` | API key, verified sender + domain auth | Fastest to ship, but the From domain must pass SPF/DKIM/DMARC for `university.edu` — usually needs IT anyway |

**Deliverability is the #1 practical risk** (600 near-identical emails with an image attachment and a link = classic spam signature). Mitigations: SPF include + DKIM signing on the sending domain, DMARC `p=none` → `quarantine` after monitoring, warm up by sending in batches of 50 with jitter, plain-text alternative part, no URL shorteners, List-Unsubscribe header, and a **"resend my pass"** self-service page so a spam-foldered email isn't a support ticket.

**Email jobs are idempotent:** `email_outbox(id, event_id, registration_id, template, idempotency_key UNIQUE, status, provider_message_id, attempts, last_error)`. Worker claims with `FOR UPDATE SKIP LOCKED`, exponential backoff `[30s, 2m, 10m, 1h, 6h]`, dead-letters after 5 attempts into an admin tray. Bounce/complaint webhooks flip `registration.email_deliverable=false` and raise an alert so staff can phone the attendee.

**Templates** (Jinja2, MJML-compiled HTML): `registration_open`, `claim_invite`, `id_rejected_retry`, `manual_review_pending`, `pass_issued`, `pass_reissued`, `ticket_cancelled`, `event_reminder_24h`, `checkout_receipt` (optional).

---

## 11. Access control state machine (the four scans)

### 11.1 States and transitions
```
              ┌──────────────┐
              │ NOT_ARRIVED  │  (pass issued, never scanned)
              └──────┬───────┘
                CHECK_IN
                     ▼
              ┌──────────────┐  EXIT_BREAK   ┌──────────────┐
              │   INSIDE     │ ────────────► │ ON_BREAK     │
              │              │ ◄──────────── │              │
              └──────┬───────┘   RE_ENTRY    └──────┬───────┘
               CHECK_OUT                      CHECK_OUT (direct)
                     ▼                               ▼
              ┌──────────────────────────────────────┐
              │              DEPARTED                │  (terminal)
              └──────────────────────────────────────┘
   any state ──admin──► REVOKED (terminal, denies all scans)
```

| # | Action | Allowed from | Result | Operator prompt |
|---|---|---|---|---|
| 1 | `CHECK_IN` | `NOT_ARRIVED` | `INSIDE` | auto (only legal action) |
| 2 | `EXIT_BREAK` | `INSIDE` | `ON_BREAK` | **operator chooses** exit vs check-out |
| 3 | `RE_ENTRY` | `ON_BREAK` | `INSIDE` | auto |
| 4 | `CHECK_OUT` | `INSIDE`, `ON_BREAK` | `DEPARTED` | **operator chooses** (from `INSIDE`) / confirm (from `ON_BREAK`) |

So a normal pass is scanned exactly 4 times; a pass with no break is scanned twice (`CHECK_IN`, `CHECK_OUT`). `breaks_used` is a counter on the pass; `EXIT_BREAK` is refused when `breaks_used >= config.access.max_breaks` (default 1) — the UI then only offers `CHECK_OUT`.

### 11.2 Server-side transition rules
The server, not the phone, decides what is legal. `POST /api/v1/scans` responds with either a completed transition or an **`action_required`** payload listing legal actions; the client shows those buttons and re-posts with the chosen `action` plus the returned `decision_token` (60 s TTL, single-use, binds the choice to that specific scan attempt). This prevents a second gate from racing the same pass while the operator is deciding.

Guards enforced server-side:
- `now` within `[checkin_opens_at, checkout_auto_at + grace]`
- `pass.status == ACTIVE`, `serial` matches, token signature and `exp` valid
- gate permission: `scan.gate_id ∈ staff.gate_ids`; gates can be typed `ENTRY_ONLY | EXIT_ONLY | BOTH`
- re-entry window: `RE_ENTRY` refused if `now - exited_at > reentry_window_minutes` → falls to `MANUAL_OVERRIDE` with reason
- occupancy cap: `CHECK_IN`/`RE_ENTRY` refused (soft-warn, overridable by MANAGER) when `INSIDE count >= hard_cap`
- **anti-passback / duplicate-scan suppression:** an identical `(pass_id, action)` within `duplicate_window_seconds` (default 10) returns the *previous* result with `duplicate: true` instead of erroring — operators double-tap constantly, and an error screen makes them re-scan and panic.

### 11.3 Concurrency
Every scan runs in one transaction:
```sql
BEGIN;
SELECT * FROM passes WHERE id = $1 FOR UPDATE;      -- row lock, serialises two gates
-- validate current_state vs requested action
INSERT INTO scans (...) RETURNING id;               -- append-only, never updated
UPDATE passes SET current_state=$2, breaks_used=..., last_scan_at=now(), version=version+1
  WHERE id=$1 AND version=$3;                        -- optimistic guard belt-and-braces
COMMIT;
```
`scans` also carries `idempotency_key UNIQUE (pass_id, client_scan_uuid)` so an offline device replaying its queue can't double-count.

### 11.4 Offline mode (non-negotiable for a gate)
The scanner PWA:
1. On shift start (online), downloads a **signed manifest**: all `ACTIVE` passes for its event as `{pass_id_short, serial, name_initials, state, photo_thumb_url?}` + the 24 h gate verification key. ~600 rows ≈ 60 KB. Stored in IndexedDB, encrypted with a key held in memory only, wiped on logout/shift end.
2. Offline scan: verifies HMAC locally, checks the manifest state, records the transition into a local queue, shows **"PROVISIONAL — verify identity manually"** with a distinct amber UI. ID photo is *not* cached by default (PII on a shared phone) — configurable `cache_id_thumbs` for gates that need it, with 4 h expiry.
3. On reconnect: replays the queue with `client_scan_uuid` + `occurred_at`. Server resolves conflicts by **occurred_at ordering**, and any transition that is illegal in the final ordering is recorded as `scans.conflict=true` and surfaced in a reconciliation report rather than silently dropped.
4. Hard cap: offline queue > 200 or age > 60 min → the app blocks further offline scans and demands connectivity. Prevents unbounded divergence.

### 11.5 Scanner operator flow (UX contract)
```
[Camera view, continuous scan]
   │ QR decoded (BarcodeDetector API, jsQR fallback)
   ▼
POST /api/v1/scans {token, gate_id, device_id, client_scan_uuid, occurred_at}
   │
   ├─ 200 {status:"ACTION_REQUIRED", participant, id_image_url, legal_actions, decision_token}
   │     ▼
   │   Full-screen card:  ┌─────────────────────────────┐
   │                      │  PHOTO FROM UPLOADED ID     │  ← large, left
   │                      │  Name · NetID · Dept · Type │
   │                      │  State: INSIDE · Break 0/1  │
   │                      │  [ EXIT (break) ] [ CHECK OUT ] [ DENY ]
   │                      └─────────────────────────────┘
   │     operator eyeballs face vs photo → taps action
   │     ▼
   │   POST /api/v1/scans/confirm {decision_token, action}
   │     ▼
   │   ✅ big green "CHECKED IN — Aarav S." + haptic + tone (2 s auto-dismiss, hands-free)
   │
   ├─ 200 {status:"DONE"}          → same success screen (auto-decided transitions)
   ├─ 409 {status:"INVALID_STATE"} → red screen, reason, [Manual override] (supervisor PIN)
   ├─ 403 {status:"REVOKED"}       → red, do not admit, contact desk
   └─ 404 {status:"UNKNOWN_PASS"}  → red, possible forgery, log + alert
```
Design rules that matter at a real gate: high-contrast colour-blind-safe result screens (green/red **plus** icon plus text), audio + haptic feedback, one-handed reachable buttons, screen-wake lock, torch toggle, and a persistent header showing gate name, operator NetID, online/offline pill, and queue depth.

**The signed `id_image_url`** is a 90-second V4 signed GCS URL, issued only inside an `ACTION_REQUIRED` response, logged in the audit trail with the scanner's identity. This is the narrow, auditable channel through which `SECURITY` ever sees PII.

---

## 12. Database schema (PostgreSQL 16)

Conventions: `uuid v7` primary keys (time-sortable, index-friendly — `uuid_generate_v7()` via `pg_uuidv7` or generated app-side), `timestamptz` everywhere (store UTC, render in `event.timezone`), `citext` for emails/NetIDs, native enums, `updated_at` via trigger, soft-delete only where legally needed (otherwise hard delete + audit row).

```sql
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE ticket_status AS ENUM (
  'PURCHASED','CLAIMED','PENDING_OCR','MANUAL_REVIEW','RETRY_REQUIRED',
  'VERIFIED','PASS_ISSUED','REJECTED','EXPIRED_UNCLAIMED','CANCELLED','REVOKED');
CREATE TYPE pass_state    AS ENUM ('NOT_ARRIVED','INSIDE','ON_BREAK','DEPARTED','REVOKED');
CREATE TYPE scan_action   AS ENUM ('CHECK_IN','EXIT_BREAK','RE_ENTRY','CHECK_OUT','DENIED','MANUAL_OVERRIDE');
CREATE TYPE staff_role    AS ENUM ('PLATFORM_ADMIN','EVENT_OWNER','EVENT_MANAGER','VERIFIER','SECURITY');
CREATE TYPE verify_outcome AS ENUM ('AUTO_PASS','MANUAL_REVIEW','RETRY_REQUIRED','APPROVED','REJECTED');
CREATE TYPE gate_type     AS ENUM ('ENTRY_ONLY','EXIT_ONLY','BOTH');

-- ───────────────────────── tenancy ─────────────────────────
CREATE TABLE events (
  id              uuid PRIMARY KEY,
  slug            citext UNIQUE NOT NULL,
  name            text NOT NULL,
  description     text,
  venue           text,
  timezone        text NOT NULL DEFAULT 'Asia/Kolkata',
  starts_at       timestamptz NOT NULL,
  ends_at         timestamptz NOT NULL,
  registration_opens_at timestamptz,
  registration_closes_at timestamptz,
  status          text NOT NULL DEFAULT 'DRAFT',  -- DRAFT|LIVE|CLOSED|ARCHIVED
  config          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ends_after_start CHECK (ends_at > starts_at)
);

CREATE TABLE gates (
  id         uuid PRIMARY KEY,
  event_id   uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  name       text NOT NULL,
  type       gate_type NOT NULL DEFAULT 'BOTH',
  is_active  boolean NOT NULL DEFAULT true,
  UNIQUE (event_id, name)
);

CREATE TABLE users (                       -- anyone who ever authenticated
  id            uuid PRIMARY KEY,
  ms_object_id  text UNIQUE,               -- Entra 'oid' — immutable identity key
  net_id        citext,
  email         citext,
  display_name  text,
  auth_method   text NOT NULL DEFAULT 'MICROSOFT_SSO',  -- or EMAIL_OTP
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON users (net_id);

CREATE TABLE event_staff (
  id         uuid PRIMARY KEY,
  event_id   uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id    uuid REFERENCES users(id),
  net_id     citext NOT NULL,              -- pre-provision before first login
  role       staff_role NOT NULL,
  gate_ids   uuid[] NOT NULL DEFAULT '{}',
  is_active  boolean NOT NULL DEFAULT true,
  added_by   uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, net_id, role)
);

CREATE TABLE scanner_devices (
  id           uuid PRIMARY KEY,
  event_id     uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  device_uuid  text NOT NULL,
  label        text,
  approved_by  uuid,
  approved_at  timestamptz,
  is_active    boolean NOT NULL DEFAULT true,
  last_seen_at timestamptz,
  UNIQUE (event_id, device_uuid)
);

-- ───────────────────────── ticketing ─────────────────────────
CREATE TABLE ticket_sources (
  id           uuid PRIMARY KEY,
  event_id     uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  source_type  text NOT NULL,              -- google_sheet|payment_webhook|csv_upload|manual
  config       jsonb NOT NULL,             -- {sheet_id, tab, column_map, watermark_col}
  last_synced_at timestamptz,
  last_watermark text,
  is_active    boolean NOT NULL DEFAULT true
);

CREATE TABLE orders (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  source_id       uuid REFERENCES ticket_sources(id),
  external_id     text NOT NULL,           -- stable hash of the source row
  purchaser_name  text,
  purchaser_email citext NOT NULL,
  purchaser_phone text,
  net_id_hint     citext,
  quantity        int  NOT NULL DEFAULT 1 CHECK (quantity BETWEEN 1 AND 20),
  ticket_type     text NOT NULL DEFAULT 'GENERAL',
  amount_paid     numeric(10,2),
  currency        char(3) DEFAULT 'INR',
  payment_status  text NOT NULL DEFAULT 'PAID',   -- PAID|PENDING|FAILED|COMP|REFUNDED
  payment_ref     text,
  purchased_at    timestamptz,
  raw_payload     jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, external_id)
);

CREATE TABLE tickets (
  id            uuid PRIMARY KEY,
  event_id      uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  order_id      uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  ticket_ref    text NOT NULL,             -- public short ref, e.g. TF26-7K3QD9
  claim_code    text,                      -- null once claimed
  seq_in_order  int NOT NULL DEFAULT 1,
  status        ticket_status NOT NULL DEFAULT 'PURCHASED',
  user_id       uuid REFERENCES users(id),
  bound_net_id  citext,
  bound_at      timestamptz,
  cancelled_reason text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_id, ticket_ref)
);
-- one identity ↔ one live ticket per event
CREATE UNIQUE INDEX tickets_one_per_identity
  ON tickets (event_id, user_id)
  WHERE user_id IS NOT NULL AND status NOT IN ('CANCELLED','REVOKED','REJECTED');
CREATE INDEX ON tickets (event_id, status);
CREATE INDEX ON tickets (order_id);

-- ─────────────────────── verification ───────────────────────
CREATE TABLE registrations (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  ticket_id       uuid NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
  user_id         uuid NOT NULL REFERENCES users(id),
  full_name       text NOT NULL,
  net_id          citext NOT NULL,
  email           citext NOT NULL,
  phone           text,
  department      text,
  year_of_study   text,
  emergency_contact text,
  dietary_notes   text,
  consent_version text NOT NULL,
  consented_at    timestamptz NOT NULL,
  attempts        int NOT NULL DEFAULT 0,
  current_id_image_id uuid,
  email_deliverable boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON registrations (event_id, net_id);

CREATE TABLE id_images (
  id            uuid PRIMARY KEY,
  event_id      uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  registration_id uuid NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
  gcs_object    text NOT NULL,
  content_type  text NOT NULL,
  bytes         int  NOT NULL,
  sha256        text NOT NULL,
  phash         text,
  width         int, height int,
  uploaded_at   timestamptz NOT NULL DEFAULT now(),
  purged_at     timestamptz
);
CREATE INDEX ON id_images (event_id, phash);
CREATE INDEX ON id_images (sha256);

CREATE TABLE verifications (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  registration_id uuid NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
  id_image_id     uuid NOT NULL REFERENCES id_images(id),
  attempt_no      int NOT NULL,
  engine          text NOT NULL,           -- gcv|paddle
  ocr_raw         jsonb,
  extracted       jsonb,                   -- {name, net_id, institution, expiry, has_photo}
  subscores       jsonb,                   -- {name:0.91, netid:1.0, ...}
  score           numeric(4,3),
  fraud_flags     text[] NOT NULL DEFAULT '{}',
  outcome         verify_outcome NOT NULL,
  failure_reasons text[] NOT NULL DEFAULT '{}',
  reviewed_by     uuid REFERENCES users(id),
  reviewed_at     timestamptz,
  review_note     text,
  latency_ms      int,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (registration_id, attempt_no)
);
CREATE INDEX ON verifications (event_id, outcome, created_at);

-- ───────────────────────── access ─────────────────────────
CREATE TABLE passes (
  id             uuid PRIMARY KEY,
  event_id       uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  ticket_id      uuid NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
  registration_id uuid NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
  serial         int  NOT NULL DEFAULT 1,
  short_code     text NOT NULL,            -- human fallback, e.g. 7K3-QD9
  qr_object      text,                     -- gcs path of rendered png
  current_state  pass_state NOT NULL DEFAULT 'NOT_ARRIVED',
  breaks_used    int NOT NULL DEFAULT 0,
  first_checkin_at timestamptz,
  last_exit_at   timestamptz,
  checked_out_at timestamptz,
  last_scan_at   timestamptz,
  revoked_at     timestamptz,
  revoked_reason text,
  version        int NOT NULL DEFAULT 0,
  issued_at      timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz NOT NULL,
  UNIQUE (event_id, short_code)
);
CREATE INDEX ON passes (event_id, current_state);

CREATE TABLE scans (                        -- APPEND ONLY. never UPDATE, never DELETE.
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  pass_id         uuid REFERENCES passes(id),
  gate_id         uuid REFERENCES gates(id),
  scanned_by      uuid REFERENCES users(id),
  device_id       uuid REFERENCES scanner_devices(id),
  action          scan_action NOT NULL,
  from_state      pass_state,
  to_state        pass_state,
  result          text NOT NULL,            -- OK|DENIED|DUPLICATE|CONFLICT|OVERRIDE
  deny_reason     text,
  override_reason text,
  offline         boolean NOT NULL DEFAULT false,
  conflict        boolean NOT NULL DEFAULT false,
  client_scan_uuid uuid NOT NULL,
  occurred_at     timestamptz NOT NULL,     -- device time (offline-safe)
  recorded_at     timestamptz NOT NULL DEFAULT now(),
  raw_token_hash  text,                     -- sha256 of presented token, for forensics
  UNIQUE (pass_id, client_scan_uuid)
);
CREATE INDEX ON scans (event_id, occurred_at DESC);
CREATE INDEX ON scans (pass_id, occurred_at);
CREATE INDEX ON scans (event_id, gate_id, occurred_at DESC);

-- ───────────────────────── ops ─────────────────────────
CREATE TABLE email_outbox (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  registration_id uuid REFERENCES registrations(id) ON DELETE CASCADE,
  to_email        citext NOT NULL,
  template        text NOT NULL,
  payload         jsonb NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  status          text NOT NULL DEFAULT 'QUEUED',   -- QUEUED|SENDING|SENT|FAILED|DEAD
  attempts        int NOT NULL DEFAULT 0,
  provider_message_id text,
  last_error      text,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  sent_at         timestamptz
);
CREATE INDEX ON email_outbox (status, next_attempt_at);

CREATE TABLE ingestion_errors (
  id uuid PRIMARY KEY, event_id uuid NOT NULL, source_id uuid,
  row_ref text, reason text NOT NULL, raw jsonb,
  resolved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE audit_log (                    -- append only, exported to GCS monthly
  id          bigserial PRIMARY KEY,
  event_id    uuid,
  actor_user_id uuid,
  actor_net_id citext,
  actor_role  staff_role,
  action      text NOT NULL,                -- e.g. VIEW_ID_IMAGE, APPROVE_VERIFICATION
  object_type text, object_id uuid,
  before      jsonb, after jsonb,
  ip_hash     text, user_agent text,
  created_at  timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON audit_log (event_id, created_at DESC);
CREATE INDEX ON audit_log (object_type, object_id);
```

**Derived read model:** a materialised view `mv_event_funnel` (per event: purchased / claimed / pending / review / issued / checked-in / inside / departed), refreshed every 30 s by the worker, backs the dashboard without hammering the write path. Live occupancy specifically is kept in Redis (`occ:<event_id>` INCR/DECR inside the same request, reconciled from Postgres every 60 s) so the dashboard is real-time.
---

## 13. API surface (FastAPI, `/api/v1`)

All responses use a single envelope for errors: `{"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}`. All mutating endpoints accept `Idempotency-Key`. All list endpoints use keyset pagination (`?after=<cursor>&limit=`). Every endpoint declares Pydantic request/response models — no bare dicts.

### 13.1 Public / auth
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` `/readyz` | — | liveness / readiness (readyz checks DB + Redis) |
| GET | `/events/{slug}/public` | — | name, dates, venue, branding, registration window |
| GET | `/auth/microsoft/start` | — | begin OIDC (event in signed state) |
| GET | `/auth/microsoft/callback` | — | code exchange, session issue, redirect to `next` |
| POST | `/auth/otp/request` · `/auth/otp/verify` | — | external-attendee fallback (rate-limited 3/15 min) |
| POST | `/auth/logout` | session | destroy session |
| GET | `/me` | session | identity + roles + event memberships |

### 13.2 Participant
| Method | Path | Purpose |
|---|---|---|
| GET | `/events/{slug}/my-ticket` | resolve session → ticket; **404 if not a ticket holder** (this is the gate) |
| POST | `/tickets/claim` | `{claim_code}` → bind ticket to session identity |
| POST | `/registrations` | create/update registration details (idempotent upsert on ticket) |
| POST | `/registrations/{id}/id-image/upload-url` | returns V4 signed resumable URL + object key |
| POST | `/registrations/{id}/id-image/complete` | verify object, enqueue OCR |
| GET | `/registrations/{id}/status` | polling fallback for verification state |
| GET | `/registrations/{id}/events` | **SSE** stream of verification progress |
| GET | `/my/pass` | pass payload + QR png signed URL (own pass only) |
| POST | `/my/pass/resend` | re-queue confirmation email (2/hour) |

### 13.3 Scanning
| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/scans` | SECURITY+ | present token → `DONE` or `ACTION_REQUIRED` |
| POST | `/scans/confirm` | SECURITY+ | `{decision_token, action}` → apply transition |
| POST | `/scans/batch` | SECURITY+ | offline queue replay (≤200 items) |
| POST | `/scans/override` | SECURITY+ w/ PIN | forced transition with mandatory reason |
| GET | `/scanner/manifest` | SECURITY+ | signed offline manifest + gate key (24 h) |
| POST | `/scanner/devices/register` | SECURITY | request approval for a device |
| GET | `/scanner/heartbeat` | SECURITY | device liveness + config refresh |

**`POST /scans` request**
```json
{ "token":"UG1.…", "gate_id":"…", "device_id":"…",
  "client_scan_uuid":"…", "occurred_at":"2026-11-08T10:12:03Z", "offline_replay":false }
```
**`ACTION_REQUIRED` response**
```json
{ "status":"ACTION_REQUIRED",
  "decision_token":"…", "expires_in":60,
  "pass":{"id":"…","short_code":"7K3-QD9","state":"INSIDE","breaks_used":0,"max_breaks":1},
  "participant":{"full_name":"…","net_id":"…","department":"…","year":"…","ticket_type":"GENERAL",
                 "id_image_url":"https://storage.googleapis.com/…signed…","id_image_expires_in":90},
  "timeline":[{"action":"CHECK_IN","at":"…","gate":"Gate A"}],
  "legal_actions":["EXIT_BREAK","CHECK_OUT"],
  "warnings":["Occupancy at 94% of cap"] }
```

### 13.4 Management
| Method | Path | Role | Purpose |
|---|---|---|---|
| POST/PATCH | `/events` `/events/{id}` | ADMIN/OWNER | create, configure |
| POST | `/events/{id}/staff` | OWNER/MANAGER | assign roles, gates |
| POST | `/events/{id}/gates` | MANAGER | manage gates |
| POST | `/events/{id}/sources/{sid}/sync` | MANAGER | manual sync |
| GET | `/events/{id}/sources/{sid}/preview` | MANAGER | column-mapping wizard |
| GET | `/events/{id}/review-queue` | VERIFIER | claimable queue |
| POST | `/verifications/{id}/approve` · `/reject` · `/request-reupload` | VERIFIER | decisions |
| POST | `/passes/{id}/revoke` · `/reissue` | MANAGER | lifecycle |
| GET | `/events/{id}/dashboard` | MANAGER | funnel + occupancy (SSE) |
| GET | `/events/{id}/exports/{kind}` | OWNER | async CSV → signed URL |
| GET | `/events/{id}/audit` | OWNER | audit log |
| GET | `/events/{id}/reconciliation` | MANAGER | offline conflicts, stuck INSIDE at close |

### 13.5 Internal (OIDC-authenticated service-to-service, not public)
`POST /internal/jobs/sync-orders` · `/internal/jobs/dispatch-email` · `/internal/jobs/refresh-views` · `/internal/jobs/purge-expired` · `/internal/webhooks/email` · `/internal/webhooks/payment/{provider}` (HMAC-verified).

### 13.6 Error codes (stable contract for the scanner UI)
`TICKET_NOT_FOUND · TICKET_ALREADY_BOUND · BINDING_CONFLICT · REGISTRATION_CLOSED · ID_UPLOAD_LIMIT · VERIFICATION_PENDING · PASS_NOT_ISSUED · PASS_REVOKED · PASS_SUPERSEDED · PASS_EXPIRED · INVALID_SIGNATURE · INVALID_STATE · DUPLICATE_SCAN · GATE_NOT_PERMITTED · OCCUPANCY_CAP · DEVICE_NOT_APPROVED · DECISION_TOKEN_EXPIRED · RATE_LIMITED`

---

## 14. Frontend (Next.js 14 App Router)

```
apps/web/src/app/
  (public)/                 page.tsx, events/[slug]/page.tsx
  (participant)/r/[ticketRef]/     page.tsx        ← server component, gated
                 register/          page.tsx        ← form + ID upload
                 status/            page.tsx        ← SSE progress
                 pass/              page.tsx        ← QR wallet
  (scanner)/scan/                   page.tsx        ← PWA, client-only
                 shift/             page.tsx        ← start/end shift, manifest sync
  (admin)/admin/[eventId]/
                 dashboard/ review/ participants/ staff/ gates/ sources/ audit/
  api/           (BFF route handlers only where a secret must stay server-side)
```
- **Gating is server-side.** `/r/[ticketRef]` is a server component that calls `my-ticket` with the forwarded session cookie; no ticket → `notFound()`. Nothing about eligibility is decided in the browser.
- **State:** TanStack Query for server state; Zustand only for scanner-local state (queue, camera, torch). No Redux.
- **Scanner is a PWA:** `next-pwa`, service worker caching the app shell, IndexedDB (`idb`) for manifest + queue, `BarcodeDetector` with `jsQR` fallback, `navigator.wakeLock`, `navigator.vibrate`.
- **SSE, not websockets**, for verification progress and the dashboard — one-directional, survives Cloud Run, trivially load-balanced. (This is the same `EventSource` pattern used in the Primus IQ frontend; reuse the reconnect/backoff hook.)
- **Accessibility:** WCAG AA contrast on result screens, `aria-live=assertive` for scan results, 48 px minimum touch targets, full keyboard path through the review console.

---

## 15. Background jobs

| Job | Trigger | Concurrency | Notes |
|---|---|---|---|
| `sync_orders` | Scheduler 2 min + manual | 1 per source (Redis lock) | incremental + nightly full reconcile |
| `ocr_verify` | enqueued on upload complete | 8 workers | 60 s timeout, 2 retries, falls to MANUAL_REVIEW on hard error |
| `render_qr` | on pass mint | 4 | idempotent by `(pass_id, serial)` |
| `dispatch_email` | every 15 s | 4 | `FOR UPDATE SKIP LOCKED`, backoff ladder |
| `refresh_funnel_mv` | 30 s | 1 | `REFRESH MATERIALIZED VIEW CONCURRENTLY` |
| `reconcile_occupancy` | 60 s | 1 | Redis counter ← Postgres truth |
| `expire_unclaimed` | hourly | 1 | T-24 h unclaimed → `EXPIRED_UNCLAIMED` + notify purchaser |
| `auto_checkout` | at `ends_at + grace` | 1 | anyone still `INSIDE`/`ON_BREAK` → `DEPARTED` flagged `auto=true` |
| `secret_expiry_watch` | daily | 1 | Entra client secret / signing key expiry alerts |
| `purge_pii` | daily | 1 | ID images at T+45 d, raw OCR at T+90 d, audit to cold storage at T+1 y |

Every job: structured log with `job`, `event_id`, `duration_ms`, `items`, `errors`; wrapped in a Redis lock (`lock:<job>:<scope>`, TTL = 2× expected runtime) so a Cloud Run scale-out never runs it twice.

---

## 16. Infrastructure (GCP) & deployment

### 16.1 Resources
| Resource | Spec | Notes |
|---|---|---|
| Cloud Run `api` | 1 vCPU / 1 GiB, min 2 (min 6 on event day), max 40, concurrency 60 | `--cpu-boost`, startup probe on `/readyz` |
| Cloud Run `web` | 1 vCPU / 512 MiB, min 1 / max 20 | |
| Cloud Run `worker` | 2 vCPU / 2 GiB, min 1 / max 10 | `--no-cpu-throttling` (background work) |
| Cloud SQL Postgres 16 | db-custom-2-8192, 100 GB SSD, HA regional, PITR 7 d | private IP + VPC connector |
| Memorystore Redis | Basic 1 GB (Standard HA on event day) | sessions, locks, queue, rate limits |
| GCS | `id-images` (45 d lifecycle), `qr` (90 d), `exports` (7 d), `audit-cold` (Nearline, 3 y) | UBLA, no public access |
| Secret Manager | per-event SSO secrets, signing keys, SMTP creds | IAM per service account |
| Artifact Registry | docker images | |
| Cloud Tasks / Scheduler | job triggers | OIDC-authenticated to `/internal/jobs/*` |
| Cloud Armor | rate-limit rules, geo allow-list optional, OWASP preconfigured rules | |
| Cloud Logging / Monitoring / Trace | dashboards + alerts | |

Choose one region close to campus (`asia-south1` for India) and keep everything in it — cross-region hops are the easiest 80 ms you'll ever waste.

### 16.2 Connection pooling (the classic Cloud Run + Postgres trap)
40 instances × a 10-connection SQLAlchemy pool = 400 connections; `db-custom-2-8192` tops out around 200. Fix: **PgBouncer in transaction mode** (sidecar or a small Cloud Run service) *or* cap `pool_size=3, max_overflow=2` per instance and raise `max_connections` deliberately. With asyncpg behind PgBouncer transaction pooling you must disable prepared-statement caching: `create_async_engine(..., connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0})`. Get this wrong and you'll see `DuplicatePreparedStatementError` under load only — never in dev.

### 16.3 Local dev (mirrors prod)
`docker-compose.yml`: `api`, `worker`, `web`, `postgres:16`, `redis:7`, `cloud-sql-proxy` (optional, for pointing at the cloud DB), `mailpit` (SMTP catcher), `gcs-fake` (fake-gcs-server) so nobody needs cloud creds to run the stack. `make up`, `make migrate`, `make seed`, `make test`.

> **Known gotcha, carried over from Primus IQ:** when connecting to Cloud SQL through the Auth Proxy, you cannot use the instance connection name as a TCP host — the proxy tunnels it, and asyncpg must be given `ssl=disable` because the proxy already encrypts the hop. Encode this in `settings.database_url` construction, not in ad-hoc scripts.

### 16.4 CI/CD
GitHub Actions / GitLab CI: lint (`ruff`, `mypy --strict` on `app/`, `eslint`) → unit tests → `alembic upgrade head` against an ephemeral Postgres → integration tests → build multi-stage images → push to Artifact Registry → deploy to staging → smoke test → manual approval → canary 10% on prod → full. Migrations run as a **pre-deploy Cloud Run job**, and every migration must be backward-compatible with the previous image (expand/contract pattern) since old and new revisions overlap during rollout.

### 16.5 Capacity math (600 concurrent)
- Registration burst: 600 users over ~10 min ≈ 1 req/s steady, ~40 rps at peak with page assets. Trivially served by 6 instances at concurrency 60.
- ID uploads: 600 × ~1.5 MB direct to GCS — zero API bandwidth cost. **This is the whole reason for signed URLs.**
- OCR: 600 calls at ~1.5 s each; 8 workers → ~2 min to clear the whole event. Vision quota default is well above this, but request a quota bump anyway and keep the PaddleOCR fallback warm.
- Gate: arrivals cluster hard — assume 60% of 600 arrive in 20 min → 18 scans/min across gates. Two gates at 10/min each with a 5-second human check is comfortable; three gates gives slack. Each scan is ~3 queries + 1 signed-URL mint ≈ 20 ms of DB time. Postgres is nowhere near the bottleneck; **the operator is**.
- Emails: 600 messages, batched 50/min → 12 min. Start sending as verifications complete, not in one blast at the end.

---

## 17. Security & privacy

| Area | Control |
|---|---|
| Transport | TLS 1.2+ everywhere, HSTS `max-age=63072000; includeSubDomains; preload` |
| Headers | CSP (no `unsafe-inline`; nonce-based), `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(self)` |
| AuthN | Entra OIDC + PKCE, nonce, full ID-token validation, JWKS caching |
| AuthZ | Deny-by-default FastAPI dependency `require(role, event_scope)`; RLS as second layer |
| Secrets | Secret Manager only; no secrets in env of built images; rotation runbook |
| PII minimisation | Scanner sees name/NetID/dept/photo only, for 90 s, audit-logged. No bulk PII on devices. |
| ID images | Private bucket, signed URLs only, never embedded in email, auto-purged T+45 d, access logged |
| Rate limits | Cloud Armor edge + Redis token bucket: auth 10/min/IP, OTP 3/15 min/email, upload-url 5/min/user, scans 120/min/device, exports 5/hour/user |
| Input validation | Pydantic strict models, max body 256 KB (images bypass the API entirely), image magic-byte sniffing server-side |
| Injection | SQLAlchemy Core/ORM parameterisation only; no f-string SQL anywhere, ever |
| SSRF | No user-supplied URLs are fetched; the Sheets client has a fixed allow-list |
| Audit | Append-only `audit_log` for every PII view, verification decision, override, revocation, export |
| Retention | Registrations T+1 y (anonymised after), ID images T+45 d, OCR raw T+90 d, audit 3 y, scans 3 y (anonymised) |
| Consent | Explicit consent checkbox with a versioned privacy notice; store `consent_version` + timestamp |
| Legal | India DPDP Act 2023: purpose limitation, stated retention, grievance contact, breach notification runbook. Get the ID-card-collection notice reviewed by the university DPO before event #1. |
| Abuse | Fraud flags feed a per-event "suspicious" tray; never auto-accuse a student |

**Threat model highlights**
| Threat | Mitigation |
|---|---|
| Forwarded QR screenshot | Human face-vs-ID check at gate; single `INSIDE` state means a second person is rejected (`INVALID_STATE`); optional rotating QR |
| Forged QR | HMAC signature; unknown pass alerts |
| Replayed scan | Idempotency key + duplicate window + append-only log |
| Stolen scanner phone | Device approval, short session, remote device deactivation, no cached PII, offline scans are provisional |
| Insider (staff exfiltrating PII) | Audit trail, watermarked exports, role separation, export rate limits |
| Someone else's ID card uploaded | Name+NetID must match the *authenticated* SSO identity — this is the strong link; the cardholder's own account would be needed |
| Sheet tampering by an organiser | Ingestion audit with raw payload; any status downgrade logged; `CANCELLED` cannot be resurrected |

---

## 18. Observability

**Structured logs** (JSON, one line per request): `request_id`, `event_id`, `user_id_hash`, `role`, `route`, `status`, `duration_ms`, `db_ms`, plus domain fields (`pass_id`, `action`, `outcome`). Never log tokens, raw QR payloads (hash them), emails in full (hash + domain), or OCR text.

**Metrics (Prometheus names, exported to Cloud Monitoring):**
`http_request_duration_seconds{route,status}` · `scan_transitions_total{action,result}` · `scan_duration_seconds` · `occupancy_current{event}` · `verification_outcomes_total{outcome}` · `ocr_latency_seconds{engine}` · `manual_review_queue_depth` · `email_outbox_depth{status}` · `sheet_sync_rows_total{result}` · `offline_replay_conflicts_total` · `db_pool_in_use`.

**Alerts (page vs ticket):**
| Condition | Severity |
|---|---|
| p95 scan latency > 500 ms for 2 min | page |
| Any gate device offline > 5 min during event window | page |
| `email_outbox` DEAD > 5 | page |
| Occupancy ≥ 95% of cap | page (ops decision) |
| OCR failure rate > 25% over 50 samples | ticket |
| Manual review queue > 50 or oldest > 15 min | ticket |
| Sheet sync failed twice consecutively | ticket |
| Entra secret expiring in < 7 days | ticket |
| 5xx rate > 1% for 5 min | page |

**Event-day war room dashboard:** funnel, live occupancy sparkline, scans/min per gate, per-gate device health, review queue depth, email queue depth, error feed. One screen, no scrolling.

---

## 19. Failure modes & degradation

| Failure | Behaviour | Recovery |
|---|---|---|
| Cloud SQL failover (~30–60 s) | API returns 503 with `Retry-After`; scanner switches to offline mode automatically | auto; replay queue on reconnect |
| Redis down | Sessions invalid → fail **closed** for admin/scanner (re-login), rate limits fall back to Cloud Armor only; scanner offline mode carries the gate | restart; sessions rebuilt on login |
| Cloud Vision down/quota | Fallback PaddleOCR; if that fails, all registrations route to `MANUAL_REVIEW` with a banner to staff | quota bump / retry |
| Email provider down | Outbox accumulates, participants can self-serve `/my/pass` (link in the registration-open email, which was already delivered) | drain when up |
| Google Sheets API down | Sync retries with backoff; manual CSV upload path stays available | — |
| Entra ID outage | **Nothing works, including staff login.** Mitigation: issue "break-glass" pre-authenticated scanner shift tokens (24 h, printed QR, MANAGER-generated in advance) stored in the war-room binder | documented runbook |
| Whole platform down at gate | Printed fallback: manifest PDF sorted by short code; staff tick names on paper; entries back-loaded via CSV import with `offline=true` | reconciliation report flags them |

---

## 20. Testing strategy

- **Unit:** state-machine transition table (exhaustive — every `(state, action)` pair including illegal ones), scoring function with golden OCR fixtures, token mint/verify (tamper, expiry, wrong key, wrong serial), column-mapping parser.
- **Property-based (Hypothesis):** any random sequence of scan actions must leave the pass in a state consistent with the replayed `scans` log; `breaks_used` never exceeds `max_breaks`.
- **Integration (testcontainers Postgres + Redis + fake-gcs):** ingestion idempotency, binding conflicts, RLS enforcement (a query without `app.event_id` must return zero rows), outbox retry ladder.
- **Contract:** OpenAPI schema snapshot test; the scanner's error-code enum is asserted against the API's.
- **E2E (Playwright):** full happy path; group-booking claim; OCR fail → retry → manual approve; offline scan then replay; duplicate scan; revoked pass.
- **Load (k6):** 600 VUs registration ramp; 3 gates × 20 scans/min for 30 min; verify p95 targets and DB connection ceiling.
- **Chaos drill before every real event:** kill the API mid-scan, pull the device off wifi, expire a decision token, and run the paper fallback once. Dress rehearsal with 20 volunteers at a real door — this catches more than any test suite.

---

## 21. Configuration

```env
APP_ENV=dev|staging|prod
APP_BASE_DOMAIN=yourdomain.com
DATABASE_URL=postgresql+asyncpg://…
DB_POOL_SIZE=3
DB_MAX_OVERFLOW=2
REDIS_URL=redis://…
GCS_BUCKET_ID_IMAGES=…
GCS_BUCKET_QR=…
GCS_BUCKET_EXPORTS=…
GCP_PROJECT_ID=…
SECRET_MANAGER_PREFIX=projects/…/secrets
SESSION_TTL_SECONDS=28800
PASS_TOKEN_VERSION=1
OCR_PRIMARY=gcv
OCR_FALLBACK=paddle
EMAIL_PROVIDER=workspace_dwd|smtp_relay|sendgrid
SENTRY_DSN=…
LOG_LEVEL=INFO
```
All read once into a Pydantic `Settings` (`pydantic-settings`, `@lru_cache`). Per-event values never appear here — they live in `events.config` + Secret Manager.

---

## 22. Repository layout & agent bootstrap

### 22.1 Monorepo
```
unigate/
├── CLAUDE.md                 ← agent bootstrap (§22.2) — read first, always
├── docs/
│   ├── LLD.md                ← this document
│   ├── adr/0001-…md          ← architecture decision records
│   └── runbooks/{event-day,incident,secret-rotation}.md
├── apps/
│   ├── api/                  ← FastAPI
│   │   ├── pyproject.toml    (uv)
│   │   ├── Dockerfile
│   │   ├── alembic/versions/
│   │   └── app/
│   │       ├── main.py               # app factory, lifespan, middleware
│   │       ├── core/                 # settings, logging, security, errors, deps
│   │       ├── db/                   # engine, session, rls.py, base.py
│   │       ├── models/               # SQLAlchemy 2.0 typed ORM, one file per aggregate
│   │       ├── schemas/              # Pydantic request/response, one file per aggregate
│   │       ├── api/v1/routes/        # auth, tickets, registrations, scans, admin, internal
│   │       ├── services/             # ← ALL business logic lives here
│   │       │   ├── ingestion/        # sources/{google_sheet,webhook,csv}.py, normaliser
│   │       │   ├── identity/         # oidc.py, sessions.py, binding.py
│   │       │   ├── verification/     # image_prep.py, ocr/{gcv,paddle}.py, extract.py, scoring.py, decide.py
│   │       │   ├── passes/           # token.py (mint/verify), render.py, issue.py
│   │       │   ├── access/           # state_machine.py, scan_service.py, offline.py
│   │       │   ├── mailer/           # outbox.py, providers/, templates/
│   │       │   └── reporting/        # funnel.py, exports.py, reconciliation.py
│   │       └── workers/              # arq entrypoint + job modules
│   └── web/                  ← Next.js 14 (see §14)
├── packages/shared-types/    ← OpenAPI-generated TS client + error-code enum
├── infra/
│   ├── terraform/            ← all GCP resources, per-env workspaces
│   └── docker-compose.yml
├── scripts/                  ← seed.py, load/k6/, backfill_*.py
└── Makefile
```

### 22.2 `CLAUDE.md` — agent bootstrap block

> Drop this file at the repo root before you start building. It is what makes a local Opus / Claude Code session infer the Primus IQ conventions instead of inventing new ones.

```markdown
# CLAUDE.md — unigate

## Read order (do this before writing any code)
1. `docs/LLD.md` — the authoritative spec. Sections 11 (state machine), 12 (schema),
   13 (API contract) are binding. Do not improvise around them.
2. `docs/adr/` — decisions already made. If your plan contradicts one, stop and say so.
3. This file — conventions and guardrails.

## Project shape — mirror the Primus IQ structure
This repo deliberately reuses the architecture, layering and tooling conventions of the
Primus IQ platform. When you need a pattern that is not spelled out here, infer it from
Primus IQ rather than from generic tutorials. Specifically:

- **Layering (strict).** `routes/` are thin: validate → authorize → call a service → shape a
  response. **All** domain logic lives in `services/`. Services never import FastAPI. Models
  never import services. A route file longer than ~120 lines means logic leaked upward.
- **Async all the way down.** SQLAlchemy 2.0 async ORM + asyncpg. No sync DB calls, no
  `requests`, use `httpx.AsyncClient` from a shared pooled client. Blocking CPU work
  (image prep, OCR post-processing) goes through `run_in_threadpool` or into the worker.
- **Pydantic at every boundary.** Settings, request/response, job payloads, and the JSONB
  `events.config` are all typed models. No bare dicts crossing a module boundary.
- **Alembic for every schema change.** Autogenerate, then hand-edit. Expand/contract only —
  a migration must be safe while the previous revision is still serving traffic.
- **Containerised dev parity.** `docker compose up` must give a fully working stack with no
  cloud credentials (fake-gcs, mailpit, local postgres/redis). `uv` for Python deps.
- **Frontend streaming.** Progress and dashboards use SSE with the same reconnect/backoff
  pattern as the Primus IQ review panel; sanitise any server-provided HTML with DOMPurify.
- **Cloud SQL Auth Proxy gotcha.** The instance connection name is not a TCP host. When the
  proxy is in play, build the URL against `127.0.0.1:5432` and pass `ssl=disable` to asyncpg
  — the proxy terminates encryption. Put this in settings construction, not in scripts.
- **Enforce behaviour with tooling, not prose.** A lesson carried over from Primus IQ: when a
  rule matters, make the code path enforce it (a dependency, a DB constraint, a returned
  `REQUIRED NEXT STEP`), not a comment or a docstring asking nicely. Examples here: RLS
  policies, partial unique indexes, the server-issued `decision_token`, append-only `scans`.
- **Derive gates dynamically.** Never hardcode a set of "known" enum values in a validation
  gate — compute it from the source of truth at import time. (The Primus IQ `BLOCK_NAMES`
  bug: a static legacy set silently dropped newly registered types.)

## Deliverable conventions
- Produce **complete, drop-in files**, not diffs or fragments. Every file you output must be
  usable as-is with no assembly.
- Make **surgical** changes. Do not reformat, reorder, or "improve" code outside the scope
  you were given.
- Diagnose before you implement: state the root cause and wait for confirmation before a
  non-trivial fix. Show logs/test output as evidence.
- Two-line summaries by default. Expand only when asked.
- Distinguish **tools** (callable functions) from **services** (domain modules) in naming and
  in conversation.

## Guardrails
- Never put PII in logs; hash emails and NetIDs, hash QR tokens.
- Never write raw SQL with f-strings.
- Never let `SECURITY`-role code paths return more participant fields than §11.5 lists.
- Never add a scan mutation that is not an append to `scans` + a guarded `UPDATE passes`.
- If a task would require breaking the state machine in §11, stop and raise it.

## Build order
Follow `docs/LLD.md` §24 milestones. Do not start M3 before M2's tests pass.
```

### 22.3 How to invoke it locally
```
claude "Read CLAUDE.md and docs/LLD.md. Implement Milestone M2 (§24):
 models + Alembic migration + services/access/state_machine.py + the /scans
 and /scans/confirm routes, with the exhaustive transition tests from §20.
 Complete drop-in files only."
```

---

## 23. Milestones

| # | Milestone | Contents | Exit criterion |
|---|---|---|---|
| M0 | Skeleton | repo, compose, settings, health, CI, Alembic base | `docker compose up` green, CI passing |
| M1 | Tenancy + auth | events, staff, RLS, Entra OIDC, sessions, `/me` | staff logs in, RLS test passes |
| M2 | Ticketing + access core | orders/tickets/passes/scans models, state machine, token mint/verify, scan APIs | exhaustive transition tests + property tests green |
| M3 | Ingestion | Sheets adapter, column wizard, idempotent sync, errors tray | 500-row sheet syncs twice with zero dupes |
| M4 | Registration + OCR | binding, upload URLs, OCR pipeline, scoring, review console | 50 sample cards scored; manual queue works |
| M5 | Pass + email | QR render, outbox, templates, resend, bounce handling | end-to-end: sheet row → email with QR |
| M6 | Scanner PWA | camera, action screens, offline manifest + queue + replay | airplane-mode gate drill passes |
| M7 | Dashboards + exports | funnel MV, occupancy, audit, CSV, reconciliation | war-room screen live |
| M8 | Hardening | load test, Cloud Armor, alerts, runbooks, DPO review, dress rehearsal | 600-VU k6 within SLOs |

---

## 24. Open decisions (need your call)

1. **Payment truth.** Is the Google Form pre-paid (external gateway, Sheet has a payment column) or does the Form *itself* collect intent with payment reconciled by hand? This decides whether `payment_status` is authoritative or advisory.
2. **External attendees.** Do alumni/guests/speakers attend? If yes, the `EMAIL_OTP` path is mandatory, not optional.
3. **Per-event Entra app registrations.** Will IT actually create one app registration per event, or one shared app with per-event app roles? The shared-app model is far less operational pain; the design supports both, but per-event means a new secret to rotate every time.
4. **Mail identity.** Does IT grant domain-wide delegation for Gmail API sending, or will you get an SMTP mailbox password per event? Affects the `mailer/providers` implementation you build first.
5. **ID card policy.** Which ID types count, and what happens to a student who genuinely lost their card? Define the override path before event day, not during it.
6. **Break policy.** Is one break the rule for all events, or per-ticket-type (e.g. volunteers get unlimited)? Currently `max_breaks` is per-event; per-ticket-type is a small extension.
7. **Occupancy cap enforcement.** Hard block at 600 or warn-and-allow-with-override? Fire-safety policy usually forces hard block.
8. **Retention.** 45 days for ID images is my proposal. Confirm with the university DPO — it may need to be 7 days, or the images may not be storable at all post-verification (in which case: OCR, score, store only the extracted fields + a hash, and show the scanner a cropped *photo region* rather than the full card).
