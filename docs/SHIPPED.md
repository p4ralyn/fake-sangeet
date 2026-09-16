# unigate — shipped checkpoint

Repo: https://github.com/p4ralyn/unigatev1 (private)
Last updated: M1 complete + participant frontend complete.

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

## Not started

- [ ] **M2** — tickets, passes, scans, the four-scan state machine, token
      mint/verify, exhaustive transition tests
- [ ] **M3** — Google Sheet ingestion, cohort A, "your ticket is ready" email
- [ ] **M4** — registration, ID upload, OCR, scoring, the retry loop, review queue
- [ ] **M5** — holds, seat inventory under concurrency, UPI proof, admin
      approval, reconciliation CSV. **Addendum §A6's concurrency tests must pass
      before a single ticket is sold.**
- [ ] **M6** — QR render, confirmation email, scanner PWA, offline mode,
      dashboard, and the 20-person dress rehearsal at a real door

## Carry-forward

`docs/plans/M1-carry-forward.md` holds every deferred item with its reasoning.
The one that will bite silently: **`updated_at` goes stale at M5**, because
addendum §A1.2's raw `UPDATE event_inventory` bypasses SQLAlchemy's Python-side
`onupdate`, and nothing fails loudly when it does.
