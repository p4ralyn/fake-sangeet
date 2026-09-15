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
  | "PAYMENT_PENDING";

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
  short_code: string;
  qr_url: string;
  holder_name: string;
  seat_index: number;
  seats: number;
  state: TicketState;
}

export interface MyPassResponse {
  tickets: PassTicket[];
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
