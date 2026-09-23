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
  | "AUTH_PROVIDER_DISABLED"
  | "DOMAIN_NOT_ALLOWED"
  | "OIDC_STATE_INVALID"
  | "OIDC_TOKEN_INVALID"
  | "UNKNOWN_NET_ID"
  | "EVENT_NOT_CONFIGURED"
  | "INTERNAL"
  // domain codes — mocked until M5 (frontend-api-contract.md)
  | "SOLD_OUT"
  | "TYPE_UNAVAILABLE"
  | "HOLD_REQUIRED"
  | "HOLD_EXPIRED"
  | "LATE_NO_SEAT"
  | "UTR_ALREADY_USED"
  | "PHASE_CLOSED"
  | "RESUBMIT_LIMIT"
  | "PAYMENT_PENDING"
  // access core, M2 — apps/api/app/core/errors.py::ErrorCode (LLD-v1 §13.6).
  // One code per refusal reason: at a gate, "why" decides whether the
  // volunteer waves someone through, sends them to the desk, or calls
  // security, so the UI branches on these by name, never by HTTP status.
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
// Availability — GET /api/v1/availability (poll every 15s)
// ---------------------------------------------------------------------------

export type TicketTypeCode = "SINGLE" | "COUPLE";

export interface TicketTypeAvailability {
  code: TicketTypeCode;
  label: string;
  /** Rupees, whole units — see LLD-v2 §4 for the UPI amount encoding. */
  price: number;
  /** Seats this ticket type consumes: 1 for SINGLE, 2 for COUPLE. */
  seats: number;
  /** True only while seats_available >= this type's `seats` (addendum §A1.1). */
  available: boolean;
}

export interface AvailabilityResponse {
  seats_total: number;
  seats_available: number;
  types: TicketTypeAvailability[];
}

// ---------------------------------------------------------------------------
// Details form — addendum §A2 step [2]. Not its own endpoint; folded into
// the hold request body.
// ---------------------------------------------------------------------------

export interface PartnerDetailsSelfClaim {
  mode: "SELF_CLAIM";
  partner_name: string;
  partner_email: string;
}

export interface PartnerDetailsProxy {
  mode: "PROXY";
  partner_name: string;
}

export interface RegistrationDetails {
  full_name: string;
  net_id: string;
  phone: string;
  department: string;
  year: string;
  /** Present only when ticket_type is COUPLE (addendum §A3). */
  partner?: PartnerDetailsSelfClaim | PartnerDetailsProxy;
  consent: boolean;
}

// ---------------------------------------------------------------------------
// Payments — mocked until M5
// ---------------------------------------------------------------------------

export interface HoldRequest {
  ticket_type: TicketTypeCode;
  details: RegistrationDetails;
}

export interface HoldResponse {
  intent_id: string;
  ticket_type: TicketTypeCode;
  seats: number;
  amount: number;
  hold_expires_at: string;
  reference_code: string;
}

export interface PaymentPageResponse {
  amount: number;
  upi_vpa: string;
  upi_qr_url: string;
  upi_deep_link: string;
  reference_code: string;
  hold_expires_at: string;
}

export interface ProofUrlResponse {
  upload_url: string;
  object_key: string;
}

export interface SubmitProofRequest {
  utr: string;
  payer_name: string;
  payer_phone: string;
  object_key: string;
}

export type PaymentState =
  | "HOLD"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELLED"
  | "CLOSED";

export type PaymentFlag = "LATE_NO_SEAT" | (string & {});

export interface SubmitProofResponse {
  state: PaymentState;
  flags?: PaymentFlag[];
}

export interface PaymentStatusResponse {
  state: PaymentState;
  reason?: string;
  flags: PaymentFlag[];
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
}

export interface MyPassResponse {
  tickets: PassTicket[];
  /** Null when there is nothing transferable. */
  transfer: TransferWindow | null;
}

/** When transfer stops. Three hours before doors, decided by the server. */
export interface TransferWindow {
  open: boolean;
  /** ISO 8601. */
  closes_at: string;
}

export interface TransferResponse {
  ticket_ref: string;
  to_net_id: string;
  /** Always true: the new holder starts the ID check again. */
  verification_required: boolean;
}

// ---------------------------------------------------------------------------
// Admin — payments review queue
// ---------------------------------------------------------------------------

export interface AdminPaymentListItem {
  id: string;
  submitted_at: string;
  payer_name: string;
  amount: number;
  utr: string;
  proof_url: string;
  flags: PaymentFlag[];
}

export interface AdminPaymentListResponse {
  items: AdminPaymentListItem[];
}

export interface AdminApproveRequest {
  /** Only meaningful as a query param (`?force=true`) on the approve call. */
  force?: boolean;
}

export interface AdminRejectRequest {
  reason: string;
  allow_resubmit: boolean;
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
