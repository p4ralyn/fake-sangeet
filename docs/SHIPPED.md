# unigate — shipped checkpoint

Repo: https://github.com/p4ralyn/unigatev1 (private)
Last updated: M1 + M2 complete, M3 and scanner in flight. 208 tests.

Legend: `[x]` shipped and verified · `[~]` in progress · `[ ]` not started

---

## M1 — Skeleton + auth ✅ COMPLETE

**Done when** (LLD-v2 §11): `docker compose up` gives a working stack,
`alembic upgrade head` runs clean, OWNER and a test VISITOR both log in,
`/me` returns the right roles. **All verified from a clean slate.**

### Environment
- [x] `docker compose up` brings the whole stack: postgres 16, redis 7, mailpit,
      fake-gcs-server, api
- [x] Zero cloud credentials needed to run or test anything
- [x] `uv` for Python deps; dev-only Dockerfile (explicitly not a cloud artifact)
- [x] `Makefile`: `up · down · logs · migrate · seed · test · lint · psql · reset`
- [x] `.env.example` covering every setting, with the Entra setup steps documented

### Data layer
- [x] Async SQLAlchemy 2.0 + asyncpg, pooled
- [x] Alembic baseline `0001_core_tables`, runs clean from an empty database
- [x] Models and migration verified in sync by an empty autogenerate diff
- [x] 7 tables: `events · gates · event_inventory · users · event_staff ·
      audit_log · funnel_events`
- [x] `event_id` on every scoped table (the multi-event seam, per LLD-v2 §0)
- [x] **`no_oversell` CHECK** — the database physically refuses to oversell,
      proven in psql, not just asserted
- [x] `seats_total_positive` and `seats_committed_non_negative` CHECKs
- [x] UUIDv7 primary keys generated app-side (no `pg_uuidv7` extension needed)
- [x] `funnel_events` from day one — abandonment data is unbackfillable

### Identity and access
- [x] `AuthProvider` protocol with a registry that fails at import if an enum
      member has no implementation
- [x] **`DevAuthProvider`** — sign in as any seeded NetID, no Azure account
      required; the whole app is runnable and testable without cloud access
- [x] **`EntraAuthProvider`** — real Microsoft OIDC, Authorization Code + PKCE
- [x] Full ID-token validation: RS256 pinned, JWKS with `kid`-miss refresh,
      `iss`, `aud`, `exp`/`nbf` with 120s skew, `tid`, and `nonce`
- [x] Dev provider is unreachable in production: `Settings` refuses to construct
      when `APP_ENV=prod` + `AUTH_PROVIDER=dev`, and the picker route 404s
      (not 403 — the route's existence is itself information)
- [x] Opaque Redis sessions, never JWT, so revocation is instant
- [x] Sliding 8h participant TTL / fixed 12h staff TTL (matches a shift)
- [x] Per-user session index so "kill every session for this person" is one call
- [x] Double-submit CSRF: header or form field, **never** the cookie
- [x] `GET /api/v1/me` returning the locked 7-field payload
- [x] `require_role` — deny by default, explicit role list per route
- [x] Staff carry VISITOR alongside their staff roles, so an OWNER can walk the
      participant purchase flow (amends LLD-v2 §2's matrix)
- [x] Login writes an audit row and a funnel row

### Seams (local substitute + cloud implementation, chosen by settings)
- [x] **Storage** — `LocalObjectStore` (fake-gcs) / `GcsObjectStore` (V4 signed
      URLs, private bucket, every blocking call off the event loop)
- [x] **Email** — one `SmtpMailer`; mailpit and Workspace both speak SMTP, so the
      difference is configuration, not code
- [x] **Auth** — dev / Entra, above
- [x] Shared pooled `httpx.AsyncClient` with lifespan disposal
- [ ] **OCR** — deliberately omitted. No implementation exists until M4, and a
      protocol with no implementation is scaffolding, not a seam.

### Guardrails proven, not just claimed
- [x] PII never logged — recursive redaction through nested dicts and lists, plus
      rule-based secret matching. `docker compose logs api | grep -c '@'` → **0**
- [x] No f-string SQL, enforced by ruff `S608`
- [x] Single error envelope `{"error": {code, message, details, request_id}}`
- [x] Thin routes, fat services — verified: no service imports FastAPI
- [x] Role sets derived from the enum at import time, never hardcoded
- [x] Seed is idempotent, proven twice against the live database
- [x] **91 tests passing**, `ruff check` clean

### Verification record
`docker compose down -v` → rebuild → migrate → seed → full suite →
OWNER login shows `["OWNER", "VISITOR"]` → owner-only route 200 → unseeded NetID
shows `["VISITOR"]` → same route 403 with the `FORBIDDEN` envelope → zero PII in
logs.

---

## Frontend — Indian wedding themed React app

Design spec: `docs/design/frontend-design-spec.md`
API contract: `docs/design/frontend-api-contract.md`

- [x] Design system shipped: invitation vernacular, zari-rule structure,
      kumkum/zari/haldi/mehendi palette, Marcellus + Mukta, no gradients,
      no shadows anywhere
- [x] Vite + React + TypeScript foundation, dev-proxied same-origin so the
      session cookie works without CORS. 4 runtime deps, no CSS framework,
      no state library, 68 kB gzipped
- [x] Typed API client, CSRF header handling, error-envelope parsing
- [x] Mock adapter with a per-endpoint real/mock switch, exercising the hard
      paths; state persists across reloads; dead-code-eliminated from prod
- [x] Role-gated routing, deny by default, `next` preserved through sign-in
- [x] Invitation landing with live seat availability, three states
- [x] Ticket type selection — COUPLE disables in place at 1 seat left (§A1.1)
- [x] Details form, then hold, then payment — the binding §A2 order, with a
      403 on the payment page bouncing silently back to step 1
- [x] Payment page: hold countdown in tabular numerals turning haldi at 5
      minutes, UPI QR, copyable VPA and amount, `upi://` deep link carrying
      amount + reference code, per-intent reference code
- [x] Client-side self-expiry into the two-path recovery, so a late payer can
      still submit proof (`LATE_NO_SEAT`) instead of vanishing
- [x] Proof submission (UTR + screenshot with type/size guard and preview)
- [x] Status screen — one honest sentence per state, no fake progress bar
- [x] The pass — card with a real perforation, QR on the stub, short code;
      a couple renders two independent cards (§A3)
- [x] Waitlist
- [x] Verified in-browser end to end: couple purchase through to two pass
      cards, zero horizontal overflow at 360px on every screen
- [ ] Admin review queue, keyboard-first
- [ ] ID verification flow (needs M4)
- [ ] Scanner PWA (M6)

---

## M2 — Access core ✅ COMPLETE

- [x] `orders · tickets · passes · scans` tables, `event_id` on every one
- [x] **`scans` append-only in the database** — triggers raise on UPDATE and
      DELETE. Proven by attempting a real UPDATE, which errors.
- [x] Four-scan state machine (LLD-v1 §11.1), pure and unit-tested
- [x] `EXIT_BREAK` refused past `max_breaks`; the server decides, never the phone
- [x] QR token: HMAC-SHA256 truncated to 128 bits over a CBOR payload
      (52 bytes vs 123 as JSON — lower density scans better at a dark door)
- [x] `serial` mismatch rejects as `SUPERSEDED`, so a reissued pass dies
      without changing `pass_id`
- [x] **Concurrency proven, not reasoned about** — 12 simultaneous CHECK_INs
      against one pass yield exactly one OK row and `version == 1`
- [x] `decision_token`, single-use, 60s, bound to one scan attempt
- [x] Duplicate suppression: a double-tap returns the previous result, not an error
- [x] SECURITY sees a separate narrow Pydantic model, never a filtered rich one
- [x] `PARTICIPANT` resolves for real against `TICKET_LIVE_STATUSES`
- [x] Per-ticket `partner_mode`, `partner_of`, `claim_code`
- [x] **208 tests**

## Ticket data — audited against the real sheets

- [x] Both client workbooks read and catalogued (`docs/design/ingestion-audit.md`)
- [x] Normaliser handling all six defect classes, self-checked
      (`scripts/sheet_normalise.py`)
- [x] **291 of 295 tickets ingestable, 435 of 441 seats**
- [x] 124 `SELF_CLAIM` couples, 22 `PROXY`
- [x] 292 phone numbers recovered from Excel's float coercion
- [x] `NEEDS REVIEW` sheet written into the client's workbook for the 4
      unreadable rows; original backed up
- [ ] **Unreconciled: 441 sold against a 600 cap leaves 159 free, not 50.**
      Either a phase-2 sheet is missing or the 550 figure was an estimate.

## In flight

- [~] **M3** — `TicketSource` adapter, idempotent row-hashed ingestion,
      `ingestion_errors`, per-ticket partner mode, seat commitment, outbox
- [~] **Scanner (part of M6)** — camera scan, decision card, four result
      states, thumb-reach ergonomics, wake lock, haptics

## Not started

- [ ] **M4** — ID upload and manual review. OCR scoring (§A4) deliberately cut:
      a human glancing at ~600 photos is hours, and it removes Cloud Vision,
      the scoring model and the retry loop from the build.
- [ ] **M5** — phase 3 purchase (holds, UPI proof, approval, reconciliation).
      **Recommended to cut**: 50 seats sold by hand into the sheet removes the
      largest remaining chunk of work for 8% of attendees.
- [ ] **M6 remainder** — QR render, confirmation email, offline scanning,
      dashboard, paper fallback export, and the dress rehearsal at a real door

## Deployed

- [x] GCE VM `unigate-vm`, e2-medium, asia-south1-a, **static IP 203.0.113.10**
- [x] `deploy/provision.sh` — dry-run by default, prints cost, idempotent
- [x] `deploy/bootstrap-vm.sh`, `deploy.sh`, `rollback.sh`
- [x] Firewall 80/443 only; 5432 deliberately shut
- [x] Dedicated service account, so M3 reads the Sheet with no key file on disk
- [ ] Domain — **not bought yet**, blocking TLS and DNS propagation
- [ ] Google OAuth client — credentials not yet created

## Carry-forward

`docs/plans/M1-carry-forward.md` holds every deferred item with its reasoning.
The one that will bite silently: **`updated_at` goes stale at M5**, because
addendum §A1.2's raw `UPDATE event_inventory` bypasses SQLAlchemy's Python-side
`onupdate`, and nothing fails loudly when it does.
