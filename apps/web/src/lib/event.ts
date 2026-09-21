/**
 * Static event details that have no home in any API schema yet.
 *
 * `EventSummary` (from GET /api/v1/me) carries only {id, slug, name} — there
 * is no date or venue field anywhere in the contract, mocked or real, and
 * apps/api is out of scope for this task. This is a single hardcoded dry-run
 * event (see CLAUDE.md "Scope discipline"), so the date and venue are fixed
 * copy here rather than plumbed through a new endpoint. If `events.config`
 * ever grows a date/venue field, replace these with values from `/me`.
 */
export const EVENT_DATE_LABEL = "Thursday, 15 October";
export const EVENT_VENUE = "UAC Terrace, 6pm";

/** Matches the fallback already used in AppShell for a signed-out visitor. */
export const EVENT_NAME_FALLBACK = "Fake Sangeet";
