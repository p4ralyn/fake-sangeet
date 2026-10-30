# Fake Sangeet — ticketing and gate access

Ticketing and door control for **Fake Sangeet — "Band Baaja No Baarat"**, a mock
Indian wedding run by Inferno (dance society) and Snuphoria (music society) at
Shiv Nadar University on 15 October 2026. It took 629 sold seats from three
Google Form sheets to 603 people through the door, with every seat tied to a
verified student ID.

The event is over and nothing is deployed. The code is published as it ran.

## What it does

- **Sheet ingestion.** Form-response workbooks (Early Bird, Phase 1, Phase 2)
  become tickets. Idempotent on a per-row ticket ref, with repair for the
  defects real form data has: packed name cells, Excel-mangled phone numbers,
  typo'd domains, malformed NetIDs. Rows that cannot be minted are recorded,
  not dropped.
- **Google sign-in**, restricted to the university domain. Roles: OWNER, ADMIN,
  SECURITY, plus VISITOR/PARTICIPANT for everyone.
- **ID verification.** Holders upload a student ID; admins review it in a live,
  first-decision-wins queue. No QR until the ID is approved.
- **Passes.** The QR is a signed token minted when `/pass` is shown, never
  emailed. Couple tickets mint two seats; a seat bought for an unnamed partner
  stays locked until it is sent to them.
- **Transfers.** A ticket moves once, ever, only after the sender's own ID is
  approved, and transfers close three hours before doors. All enforced in the
  database and services, not by convention.
- **Gate scanner** (`/scan`). Camera scanning via `BarcodeDetector` with a jsQR
  fallback. Four-scan state machine: check in, one break out, back in with the
  ID photo shown again, check out. Every scan is decided by a person; `scans` is
  append-only, enforced by triggers.
- **Ops dashboard** (`/ops`): occupancy, the door, the ID review queue and the
  mail outbox.
- **Mail outbox.** Every email is a row in `email_outbox` with an idempotency
  key, drained by a `mailer` worker over SMTP.

## Stack

| | |
|---|---|
| API | FastAPI, SQLAlchemy 2.0 async + asyncpg, Pydantic, Alembic |
| Data | PostgreSQL 16, Redis 7 (sessions, OAuth state, scan decisions) |
| Storage | GCS, or fake-gcs-server locally (ID photos) |
| Web | React 18 + Vite, TypeScript |
| Mail | SMTP; Mailpit locally |
| Deploy | One VM, docker compose, Caddy for TLS |

## Running it locally

Needs Docker only. No cloud credentials.

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python /srv/scripts/seed.py
```

| | |
|---|---|
| Web | http://localhost:5173 |
| API | http://localhost:8000 |
| Mailpit (caught mail) | http://localhost:8025 |

Locally, `AUTH_PROVIDER=dev` replaces Google with a picker of the seeded staff
NetIDs, plus a box to sign in as anyone else. Staff come from the `SEED_*`
lists in `.env`; `seed.py` reconciles them on every run.

```bash
make test      # 490 tests, pytest in the api container
make lint      # ruff
make psql      # a shell on the database
```

Stop with `docker compose down`. `down -v` deletes the database volume.

## Layout

```
apps/api/app/
  api/v1/routes/   validate, authorize, call a service, shape a response
  services/        all domain logic (access, identity, ingestion, mailer,
                   ops, passes, storage, ticketing, verification)
  models/          SQLAlchemy
  schemas/         Pydantic
apps/api/alembic/  migrations
apps/web/src/
  components/      design system primitives
  routes/          one file per screen; ops/ has its own rail
scripts/           operator tools (ingest, seed, mailer, ...), all with --dry-run
deploy/            Caddyfile, VM provisioning
docs/              design docs and specs (LLD-v2 is the main spec)
```

## Operator scripts

All take NetIDs or `@snu.edu.in` addresses and support `--dry-run`. Run them in
the api container: `docker compose run --rm api python /srv/scripts/<name>.py`.

| Script | Does |
|---|---|
| `ingest.py` | workbooks to tickets |
| `seed.py` | event config, inventory and staff roles from `.env` |
| `mailer.py` | drains the email outbox (`--watch` runs it as a service) |
| `reset_identity.py` | removes a person's tickets, passes, scans and ID checks |
| `refresh_passes.py` | invalidates a person's QRs and resets their pass |
| `issue_pass_without_id.py` | issues a pass without an ID check (audited) |
| `demo_ticket.py` | demo ticket on a test account |
| `send_sample_emails.py` | one of every email to one address, bypassing the queue |
| `audit_sheets.py`, `sheet_normalise.py` | inspect and repair workbooks before ingest |

## Design docs

Start with `docs/LLD-v2.md`, then `docs/LLD-v2-addendum-A.md` (it overrides v2
where they disagree). `docs/LLD-v1.md` has the scan state machine and QR token
format. `docs/design/` covers the frontend, the API contract and the ingestion
audit.

Student data has been removed from the code and history. NetIDs in tests and
`.env.example` are made up, apart from the owner's (`pk944`).
