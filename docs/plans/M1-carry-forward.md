# M1 — carry-forward notes

Written at the close of M1 (branch `m1-skeleton-auth`, commits `e56385a..9ef0bd9`).
Everything here was consciously deferred after review, not missed. The final
whole-branch review triaged all of it as "acceptable to carry" — none of it
blocked M1. It is recorded because the reasoning is unbackfillable.

## Must be handled by a specific later milestone

### M5 — `updated_at` will silently go stale on seat movements

`Event.updated_at` and `EventInventory.updated_at` use SQLAlchemy's Python-side
`onupdate=func.now()` (`app/db/base.py`, `app/models/event.py`). That fires only
on ORM attribute mutation plus flush.

Addendum §A1.2 mandates the inventory decision be a raw
`SELECT … FOR UPDATE` plus `UPDATE event_inventory SET seats_committed = …`.
Raw SQL bypasses `onupdate` entirely, so every seat movement will leave
`updated_at` stale — and nothing will fail loudly when it does.

**Do one of:** set `updated_at = now()` explicitly in that `UPDATE`, or convert
the column to a database-side trigger. Decide before the first seat is sold.

### M2 — `EventStaff.user_id` goes stale on identity churn

`bind_staff_rows` backfills `user_id` only `WHERE user_id IS NULL`. If a NetID is
ever re-associated with a different Entra `oid` (reissued, or a recycled NetID
given to a new person), role resolution still works — it keys on `net_id` by
design — but `EventStaff.user_id` keeps pointing at the old identity forever.

Nothing reads that column yet, so this grants no wrong privilege today. A correct
fix needs a policy decision about recycled NetIDs and reissued `oid`s, which
belongs with the identity-binding work in LLD-v1 §8.3 (`BINDING_CONFLICT`).

### M2/M5 — the seed creates gates by name, so a rename makes a second gate

`scripts/seed.py` looks up the gate by `(event_id, name)`. Changing
`EVENT_GATE_NAME` between runs creates a second gate rather than renaming the
first. Harmless with exactly one gate; revisit when gates multiply.

## Security notes that depend on framework behaviour

### `safe_next()` relies on an incidental Starlette property

`safe_next()` (`app/services/identity/oidc_state.py`) rejects `//host`, `/\host`,
and anything carrying a scheme or netloc. It uses Python's `urlsplit`, which
under-approximates WHATWG slash-collapsing for control-char-interrupted inputs
such as `/<TAB>//evil.example`.

That input is **non-exploitable today, but only because** Starlette's
`RedirectResponse` percent-encodes raw control characters before writing the
`Location` header — verified against the installed Starlette:
`RedirectResponse('/\t//evil.example').headers['location'] == '/%09//evil.example'`,
which a browser parses as a relative path.

**If `next` is ever consumed by anything other than `RedirectResponse`** — an HTML
meta-refresh, a JS-driven redirect, a template interpolation — re-audit it. The
protection is not in our code.

No test pins this. A regression test belongs with any future hardening pass.

## Accepted deviations from CLAUDE.md, with reasons

- **Bare dicts cross the `AuthProvider` boundary** (`state_payload`, `stored`).
  The payload is provider-specific opaque state that round-trips through JSON in
  Redis; typing it needs a per-provider model plus a union — more machinery than
  the dict it replaces. CLAUDE.md's Pydantic-at-boundaries rule names settings,
  request/response, job payloads and `events.config`, all stable shapes.
- **`auth.py` is 152 lines against a ~120-line guideline.** Reviewed twice: it is
  five thin endpoints plus a cookie helper, every call delegating to a service.
  One genuine leak (a staff query) was extracted. Letter missed, spirit intact.

## Minor items, safe to carry indefinitely

- `downgrade()` does not drop the `citext`/`pg_trgm`/`pgcrypto` extensions —
  other databases could depend on them; dropping is the riskier default.
- `deps.get_redis_client()` does not reset `_redis` to `None` after `aclose()`,
  while `core/http.py` does. Unreachable today (the suite never runs the ASGI
  lifespan; production runs it once at exit). Fix for consistency if the test
  harness ever recreates the app in-process.
- `destroy_user_sessions` reads `smembers` outside the transaction — a session
  created concurrently with revoke-all survives it. Close this before wiring it
  to a user-facing "sign out everywhere" button.
- `load_session`'s read-then-expire is two calls; worst case a sliding session
  misses one TTL bump.
- `local.py`'s `_ensure_bucket` does not check the creation POST status; a real
  failure surfaces as a 404 on the next `put()`.
- JWKS refresh has no backoff for repeated bogus `kid` values. Requires a real
  Entra tenant to reach, and the cost lands on Microsoft's endpoint.
- `ErrorCode.AUTH_PROVIDER_DISABLED` is defined but never raised — the dev-picker
  correctly uses `NOT_FOUND` instead ("existence is itself information"). Prune
  when `ErrorCode` is next touched.
- `extend-immutable-calls` lists `fastapi.Query`/`Security`, neither used yet.
- Test coverage gaps: `test_create_then_load_round_trips` does not assert
  `created_at` timezone-awareness or `None`-valued optionals;
  `test_ordinary_next_value_is_honoured` exercises only the default value, so it
  does not prove an arbitrary same-origin path survives `safe_next` unchanged.

## Process note worth keeping

Two defect classes recurred across this milestone and are worth watching for in
M2 onward:

1. **Copied constants drift.** A hardcoded `{"OWNER","ADMIN","SECURITY"}` set was
   flagged and fixed in `auth.py`, then reappeared in `services/audit.py` one task
   later — because it lived in the plan text, which was the source both times.
   Now single-sourced as `STAFF_ROLE_NAMES`, derived from the enum at import time
   with an `assert` pinning it.
2. **Tests that cannot fail.** Three shipped in the plan and all passed while
   proving nothing: an expiry test using `exp = now - 10` inside a 120-second
   clock-skew allowance; a PII assertion checking for `"owner@"` when the address
   is `owner.test@…`; and a test asserting on a row it never created. The habit
   that caught them — break the code, watch the test fail, then restore — is how
   every fix in this milestone was verified. Keep doing it.
