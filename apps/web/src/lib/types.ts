/**
 * TypeScript types for every payload in docs/design/frontend-api-contract.md,
 * cross-checked against apps/api/app/schemas and LLD-v2 / addendum A.
 * Later agents (participant + admin screens) import these — keep them
 * complete and accurate as the contract evolves.
 */

// ---------------------------------------------------------------------------
// Roles (source of truth: apps/api/app/services/identity/roles.py::Role)
// ---------------------------------------------------------------------------

export type Role = "OWNER" | "ADMIN" | "SECURITY" | "PARTICIPANT" | "VISITOR";

// ---------------------------------------------------------------------------
// /api/v1/me — real today
// ---------------------------------------------------------------------------

export interface EventSummary {
  id: string;
  slug: string;
  name: string;
}

export interface MeResponse {
  user_id: string;
  net_id: string;
  display_name: string;
  /** Google's `picture`, or null on the dev provider. */
  avatar_url?: string | null;
  email: string | null;
  roles: Role[];
  event: EventSummary;
  csrf_token: string;
}

// ---------------------------------------------------------------------------
// Error envelope (source of truth: apps/api/app/core/errors.py)
// ---------------------------------------------------------------------------

/** Stable error codes the UI must be able to branch on by name. */
export type ErrorCode =
  // apps/api/app/core/errors.py::ErrorCode — live today
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "VALIDATION_ERROR"
  | "CSRF_INVALID"
  | "DOMAIN_NOT_ALLOWED"
  | "OIDC_STATE_INVALID"
  | "OIDC_TOKEN_INVALID"
  | "UNKNOWN_NET_ID"
  | "EVENT_NOT_CONFIGURED"
  | "INTERNAL"
  // access core, M2 — apps/api/app/core/errors.py::ErrorCode (LLD-v1 §13.6).
  // One code per refusal reason: at a gate, "why" decides whether the
  // volunteer waves someone through, sends them to the desk, or calls
  // security, so the UI branches on these by name, never by HTTP status.
  | "UNKNOWN_PASS"
  | "INVALID_TOKEN"
  | "INVALID_SIGNATURE"
  | "TOKEN_WRONG_EVENT"
  | "PASS_EXPIRED"
  | "PASS_REVOKED"
  | "PASS_SUPERSEDED"
  | "INVALID_STATE"
  | "GATE_NOT_PERMITTED"
  | "DECISION_TOKEN_INVALID"
  | "DECISION_TOKEN_EXPIRED";

export interface ErrorDetail {
  code: ErrorCode | (string & {});
  message: string;
  details: Record<string, unknown> | null;
  request_id: string | null;
}

export interface ErrorEnvelope {
  error: ErrorDetail;
}

// ---------------------------------------------------------------------------
// Pass — GET /my/pass
// ---------------------------------------------------------------------------

export type TicketState =
  | "ISSUED"
  | "ID_PENDING"
  | "ID_VERIFIED"
  | "MANUAL_REVIEW"
  | "CHECKED_IN"
  | "EXIT_BREAK"
  | "CHECKED_OUT"
  | "REVOKED";

/**
 * Which side of the (mock) wedding a seat is on (client, 14 Oct 2026). Fixed at
 * minting -- no screen edits it -- and it follows the seat through a transfer.
 * Source of truth: `app.models.ticketing.GuestTag`.
 */
export type GuestTag = "BAARATI" | "GHARWALE";

export interface PassTicket {
  /** Empty string until a pass is issued. */
  short_code: string;
  /**
   * The QR payload — the exact string the scanner decodes and posts to
   * /api/v1/scans. Named `qr_token`, not the contract's original `qr_url`:
   * that name was written when a server-rendered PNG was assumed, and a
   * bearer credential in a field called "url" is a thing people log, paste
   * into bug reports and forward. Null until a pass exists.
   *
   * Never log it, never store it, never put it in a URL.
   */
  qr_token: string | null;
  holder_name: string;
  seat_index: number;
  seats: number;
  state: TicketState;
  guest_tag: GuestTag;
  /**
   * A PROXY couple's partner seat nobody has been given yet. Never carries a
   * QR — nobody's ID stands behind it. The screen draws a lock and the way to
   * send it to the partner instead.
   */
  locked: boolean;
}

export interface MyPassResponse {
  tickets: PassTicket[];
  /**
   * Transferring this person's OWN ticket; null when they hold none. For a
   * PROXY couple whose partner seat is unsent, both seats move together.
   */
  transfer: TransferWindow | null;
  /** Sending the locked partner seat to the partner. Null when there is none. */
  partner_transfer: TransferWindow | null;
}

/** When transfer stops. Three hours before doors, decided by the server. */
export interface TransferWindow {
  /** The time window ONLY. Whether THIS ticket may still move is
   * `transfers_used` against `max_transfers` — the two are separate because
   * "come back later" and "this can never move again" are different answers. */
  open: boolean;
  /** ISO 8601. */
  closes_at: string;
  transfers_used: number;
  max_transfers: number;
}

export interface TransferResponse {
  ticket_ref: string;
  to_net_id: string;
  /** Always true: the new holder starts the ID check again. */
  verification_required: boolean;
}

// ---------------------------------------------------------------------------
// Scans — POST /api/v1/scans, POST /api/v1/scans/confirm (SECURITY/ADMIN/OWNER)
// Source of truth: apps/api/app/schemas/scans.py, apps/api/app/models/access.py.
// The four-scan state machine is LLD-v1 §11.1; the operator UX contract is
// LLD-v1 §11.5.
// ---------------------------------------------------------------------------

/** `passes.current_state` — apps/api/app/models/access.py::PassState. */
export type PassState = "NOT_ARRIVED" | "INSIDE" | "ON_BREAK" | "DEPARTED" | "REVOKED";

/** `scans.action` — apps/api/app/models/access.py::ScanAction. */
export type ScanAction = "CHECK_IN" | "EXIT_BREAK" | "RE_ENTRY" | "CHECK_OUT" | "DENIED" | "MANUAL_OVERRIDE";

export interface ScanRequest {
  token: string;
  gate_id?: string | null;
  device_id?: string | null;
  client_scan_uuid: string;
  /** ISO 8601, the device clock — exists so an offline queue can replay in order later. */
  occurred_at: string;
  offline_replay?: boolean;
}

export interface ScanConfirmRequest {
  decision_token: string;
  action: ScanAction;
}

/**
 * What a SECURITY volunteer's phone receives — schemas.scans.SecurityScanResponse,
 * verbatim. Deliberately narrow: the API has a *second*, richer model for
 * ADMIN/OWNER (see StaffScanResponse below) rather than filtering fields at
 * runtime, specifically so a field never leaks to a SECURITY device just
 * because it was added to the rich model. There is no NetID, department,
 * ticket reference or pass id here — LLD-v1 §11.5's card sketch shows a NetID
 * line, but the shipped M2 response never carries one, for anyone.
 */
export interface SecurityScanResponse {
  status: "DONE" | "ACTION_REQUIRED";
  /** True when this is the suppressed re-serve of a very recent identical
   * scan (§11.2 anti-passback). Never render this as an error — operators
   * double-tap constantly. */
  duplicate: boolean;
  participant_name: string | null;
  /** Baarati or gharwale -- the one client addition to §11.5 (14 Oct 2026),
   * shown on the decision card and every result screen. */
  guest_tag: GuestTag | null;
  /** A 90-second signed URL, present only on ACTION_REQUIRED, null until M4
   * ships ID capture. Render a placeholder, never a broken <img>. */
  id_image_url: string | null;
  state: PassState;
  breaks_used: number;
  max_breaks: number;
  /** The buttons to render, in server-decided order. Never hardcode this set. */
  legal_actions: ScanAction[];
  /** Single-use, 60s TTL, binds an operator's choice to this scan attempt. */
  decision_token: string | null;
  decision_expires_in: number | null;
  /** The action just applied, when status is DONE. */
  action: ScanAction | null;
}

/** ADMIN/OWNER additionally get these — schemas.scans.StaffScanResponse. */
export interface StaffScanResponse extends SecurityScanResponse {
  pass_id: string;
  short_code: string;
  serial: number;
  version: number;
  from_state: PassState | null;
  scan_id: string | null;
}

export type ScanResponse = SecurityScanResponse | StaffScanResponse;

// ---------------------------------------------------------------------------
// ID verification — M4. Source of truth: apps/api/app/schemas/verification.py.
// These endpoints are REAL, not mocked: the commerce screens run on mock.ts
// because M5 does not exist, but ID upload and review are shipped backend.
// ---------------------------------------------------------------------------

/** `verifications.outcome`. There is no AUTO_PASS — OCR is cut, a person decides. */
export type VerifyOutcome = "MANUAL_REVIEW" | "APPROVED" | "RETRY_REQUIRED" | "REJECTED";

/** `tickets.status` as M4 uses it. */
export type TicketStatus =
  | "PURCHASED"
  | "CLAIMED"
  | "PENDING_OCR"
  | "MANUAL_REVIEW"
  | "RETRY_REQUIRED"
  | "VERIFIED"
  | "PASS_ISSUED"
  | "REJECTED"
  | "EXPIRED_UNCLAIMED"
  | "CANCELLED"
  | "REVOKED";

/**
 * GET /api/v1/me/verification — what a person is told about their own ID
 * check. Deliberately narrow: no image URL, no fraud flags, no reviewer
 * identity. The API has a separate, richer model for reviewers rather than
 * filtering this one.
 */
export interface MyVerification {
  ticket_status: TicketStatus;
  outcome: VerifyOutcome | null;
  attempts_used: number;
  attempts_remaining: number;
  /** The server's answer, not the client's reading of a status. Gate the
   * upload control on this and nothing else. */
  can_upload: boolean;
  blocked_reason: string | null;
  /** The reviewer's note, present only when they asked again or refused. */
  review_note: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  consent_version: string | null;
}

/** One row of GET /api/v1/admin/verifications. ADMIN/OWNER only. */
export interface ReviewItem {
  verification_id: string;
  registration_id: string;
  attempt_no: number;
  outcome: VerifyOutcome;
  full_name: string;
  net_id: string;
  email: string;
  ticket_status: TicketStatus;
  fraud_flags: string[];
  submitted_at: string;
  /** Signed, ~90s. Never cache it, never store it, never put it in a link. */
  id_image_url: string | null;
}

export interface ReviewQueue {
  pending: number;
  items: ReviewItem[];
}

export interface ReviewDecisionRequest {
  approve: boolean;
  note?: string | null;
  /** Only consulted on a refusal. */
  allow_resubmit?: boolean;
}

// ---------------------------------------------------------------------------
// Ops dashboard — ADMIN/OWNER. Source of truth: apps/api/app/schemas/ops.py.
// There is no notifications table: every item is derived from rows that
// already exist, so the feed cannot drift out of sync with reality.
// ---------------------------------------------------------------------------

export interface Occupancy {
  inside: number;
  on_break: number;
  departed: number;
  not_arrived: number;
  revoked: number;
}

export interface Seats {
  committed: number;
  /** The venue cap, always — never "what's left on sale". */
  total: number;
}

export interface Queues {
  ids_waiting: number;
  ids_oldest_minutes: number | null;
  ingestion_unresolved: number;
  mail_queued: number;
  mail_dead: number;
}

/** One side of the wedding: tickets held and who is in. Counts only. */
export interface TagCount {
  tag: GuestTag;
  tickets: number;
  inside: number;
  on_break: number;
}

export interface OpsSummary {
  occupancy: Occupancy;
  seats: Seats;
  /** Baarati then gharwale, always both, even at zero. */
  by_tag: TagCount[];
  queues: Queues;
  scans_recent: number;
  scans_window_minutes: number;
  refusals_recent: number;
  generated_at: string;
}

export interface MailProblem {
  id: string;
  to_email: string;
  template: string;
  status: string;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string | null;
}

export interface MailHealth {
  queued: number;
  sending: number;
  sent: number;
  failed: number;
  dead: number;
  problems: MailProblem[];
}
