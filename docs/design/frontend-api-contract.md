# unigate — frontend API contract

What the React app may call, and which calls are real today.

Source of truth: LLD-v2 §7, addendum §A5, and the M1 code in `apps/api`.
When a mocked endpoint lands for real, delete its mock — nothing else changes.

## Transport rules

- Same origin. Vite dev-proxies `/api` and `/auth` to `http://api:8000`, so the
  `HttpOnly; SameSite=Lax` session cookie just works. No CORS, no token in JS.
- Every mutating request sends `X-CSRF-Token`, read from `csrf_token` on the
  `/api/v1/me` response. The CSRF cookie is readable by JS but the **header is
  what the server checks** — it deliberately does not accept the cookie.
- Errors always arrive as `{"error": {"code", "message", "details", "request_id"}}`.
  Render `message` to the user; branch on `code`; log `request_id`.

## Real today (M1) — wire these for actual

| Call | Notes |
|---|---|
| `GET /api/v1/me` | `{user_id, net_id, display_name, email, roles[], event{id,slug,name}, csrf_token}`. 401 when signed out — that is the signed-out signal, not an error to surface. |
| `GET /auth/start?next=<path>` | Full-page navigate, never fetch. `next` must be a same-origin path; the server rejects anything else. |
| `POST /auth/logout` | Needs `X-CSRF-Token`. Redirects; follow it. |
| `GET /healthz`, `GET /readyz` | Ops only. |

`roles[]` is one or more of `OWNER`, `ADMIN`, `SECURITY`, `PARTICIPANT`, `VISITOR`.
In M1 a non-staff user is always `VISITOR`; `PARTICIPANT` appears at M2 once a
ticket can exist. Gate routes on roles, never on a client-side flag.

## Mocked until M5 — build against these exactly

```ts
GET  /api/v1/availability
  -> { seats_total, seats_available,
       types: [{ code: "SINGLE"|"COUPLE", label, price, seats, available }] }
  Poll every 15s. Redis-cached 10s server-side.
  A type is offered only while seats_available >= its seats (addendum §A1.1),
  so the last seat sells as a single and COUPLE greys out.

POST /api/v1/payments/hold
  body: { ticket_type, details: {...} }        // details FIRST, then the hold
  -> 201 { intent_id, ticket_type, seats, amount, hold_expires_at, reference_code }
  -> 409 { error.code: "SOLD_OUT" }            // user never reaches a payment screen

GET  /api/v1/payments/{id}/page
  -> 200 { amount, upi_vpa, upi_qr_url, upi_deep_link, reference_code, hold_expires_at }
  -> 403 unless the caller owns a live HOLD. On 403, route back to step 1.

POST /api/v1/payments/{id}/proof-url   -> { upload_url, object_key }
POST /api/v1/payments/{id}/submit
  body: { utr, payer_name, payer_phone, object_key }
  -> 200 { state: "PENDING_APPROVAL" }
  -> 409 { error.code: "UTR_ALREADY_USED" }    // surfaced immediately, not after review
  -> 200 { state: "PENDING_APPROVAL", flags: ["LATE_NO_SEAT"] }  // grace-window path

GET  /api/v1/payments/{id}/status            -> { state, reason?, flags[] }
GET  /my/pass -> { tickets: [{ short_code, qr_url, holder_name, seat_index, seats, state }] }

GET  /api/v1/admin/payments?state=PENDING_APPROVAL
  -> { items: [{ id, submitted_at, payer_name, amount, utr, proof_url, flags[] }] }
POST /api/v1/admin/payments/{id}/approve     // ?force=true may exceed the cap
POST /api/v1/admin/payments/{id}/reject      // { reason, allow_resubmit }
```

Error codes the UI must handle by name:
`SOLD_OUT · TYPE_UNAVAILABLE · HOLD_REQUIRED · HOLD_EXPIRED · LATE_NO_SEAT ·
UTR_ALREADY_USED · PHASE_CLOSED · RESUBMIT_LIMIT · PAYMENT_PENDING · FORBIDDEN ·
UNAUTHENTICATED`

## Mock adapter rules

- One module owns the switch. A per-endpoint map decides real vs mock, so a
  milestone flips one line per endpoint.
- Mocks must exercise the **hard** paths, not the happy one: sold out, only one
  seat left so COUPLE is disabled, a hold that expires while you watch, a
  duplicate UTR, a rejected payment with one resubmit left.
- Mocks are dev-only and must not ship in a production build.

## Flow order — binding (addendum §A2)

Choose type → **details form** → hold granted → **only then** the payment page.
The payment screen is unreachable without a live hold you own. A direct visit
routes back to step 1. This ordering is the mechanism that stops someone paying
into a sold-out event; do not "simplify" it into a single checkout page.
