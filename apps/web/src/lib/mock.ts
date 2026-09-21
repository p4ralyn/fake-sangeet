/**
 * The mock adapter for every endpoint marked "mocked until M5" in
 * docs/design/frontend-api-contract.md.
 *
 * This is the ONE module that owns the per-endpoint real/mock switch
 * (`ENDPOINT_MODE` below) — flipping an endpoint to real when its milestone
 * lands is a one-line change here, and nothing else in the app needs to
 * change since screens only ever call the functions exported from this file.
 *
 * The mock engine itself (fake data, the scenario state machine) is built
 * behind an `import.meta.env.DEV` guard so `vite build` (production mode)
 * dead-code-eliminates it entirely — see `buildEngine()` below.
 */
import { ApiError, api } from "./api";
import type {
  AdminPaymentListResponse,
  AdminPaymentListItem,
  AdminRejectRequest,
  AvailabilityResponse,
  HoldRequest,
  HoldResponse,
  MyPassResponse,
  PassTicket,
  PaymentPageResponse,
  PaymentState,
  PaymentStatusResponse,
  ProofUrlResponse,
  SubmitProofRequest,
  SubmitProofResponse,
  TicketTypeCode,
} from "./types";

// ---------------------------------------------------------------------------
// Per-endpoint real/mock map. Flip one line per endpoint as milestones land.
// ---------------------------------------------------------------------------

type EndpointName =
  | "availability"
  | "createHold"
  | "paymentPage"
  | "requestProofUpload"
  | "submitProof"
  | "paymentStatus"
  | "myPass"
  | "adminListPayments"
  | "adminApprovePayment"
  | "adminRejectPayment";

const ENDPOINT_MODE: Record<EndpointName, "real" | "mock"> = {
  availability: "mock",
  createHold: "mock",
  paymentPage: "mock",
  requestProofUpload: "mock",
  submitProof: "mock",
  paymentStatus: "mock",
  myPass: "mock",
  adminListPayments: "mock",
  adminApprovePayment: "mock",
  adminRejectPayment: "mock",
};

import { accountForApprovedCount } from "./upi";

// ---------------------------------------------------------------------------
// Scenario selection — a query param (`?mock_scenario=sold_out`), sticky in
// sessionStorage so client-side navigation keeps the demo state.
// ---------------------------------------------------------------------------

export const MOCK_SCENARIOS = [
  "default",
  "sold_out",
  "last_seat_couple_disabled",
  "hold_expiring",
  "duplicate_utr",
  "rejected_one_resubmit_left",
  "has_pass",
] as const;

export type MockScenario = (typeof MOCK_SCENARIOS)[number];

const SCENARIO_STORAGE_KEY = "unigate_mock_scenario";

export function getMockScenario(): MockScenario {
  if (typeof window === "undefined") return "default";
  const fromQuery = new URLSearchParams(window.location.search).get("mock_scenario");
  if (fromQuery && (MOCK_SCENARIOS as readonly string[]).includes(fromQuery)) {
    window.sessionStorage.setItem(SCENARIO_STORAGE_KEY, fromQuery);
    return fromQuery as MockScenario;
  }
  const stored = window.sessionStorage.getItem(SCENARIO_STORAGE_KEY);
  if (stored && (MOCK_SCENARIOS as readonly string[]).includes(stored)) {
    return stored as MockScenario;
  }
  return "default";
}

export function setMockScenario(scenario: MockScenario): void {
  window.sessionStorage.setItem(SCENARIO_STORAGE_KEY, scenario);
  // A scenario switch starts a fresh demo: drop any purchase carried over from
  // the previous one, or the new scenario inherits a stale hold.
  window.sessionStorage.removeItem(STATE_STORAGE_KEY);
}

// ---------------------------------------------------------------------------
// Purchase state persistence.
//
// The engine's intents live in memory, so a full page load (a refresh, or any
// navigation that is not client-side routing) used to wipe an in-flight or
// approved purchase -- a demo would reach "Approved. Your pass is ready." and
// then show "No pass yet" on /pass. Production is unaffected because the real
// API reads from Postgres, but a demo that breaks on refresh looks like a
// product bug. MockIntent is all primitives, so sessionStorage round-trips it.
// ---------------------------------------------------------------------------

const STATE_STORAGE_KEY = "unigate_mock_state";

// ---------------------------------------------------------------------------
// Demo identities.
//
// Some NetIDs are treated as having already bought before the app existed --
// they stand in for the ~441 people whose tickets arrive by sheet ingestion
// (M3), so the pass page can be shown without walking a purchase. Signing in
// as one of these and visiting /pass just works, with no scenario parameter.
//
// This is mock-layer only. Once M3 ingests the real sheets, a pass comes from
// the database and this list is deleted.
// ---------------------------------------------------------------------------
export const DEMO_PREPURCHASED: Record<
  string,
  { ticketType: TicketTypeCode; seats: number; holderName?: string }
> = {
  // holderName overrides the display name, because the dev auth provider
  // builds one from the NetID ("zq204" -> "Zq204") and a pass reading "Zq204"
  // looks wrong in a demo. Real Google sign-in supplies the actual name, so
  // this override is only needed while AUTH_PROVIDER=dev.
  zq204: { ticketType: "COUPLE", seats: 2, holderName: "Riya Sharma" },
};

const IDENTITY_STORAGE_KEY = "unigate_current_identity";

/** Written by the session provider so the mock can attribute a purchase. */
export function rememberIdentity(netId: string, displayName: string): void {
  try {
    window.sessionStorage.setItem(
      IDENTITY_STORAGE_KEY,
      JSON.stringify({ netId: netId.toLowerCase(), displayName }),
    );
  } catch {
    // Demo convenience only; never break the app over storage.
  }
}

function currentIdentity(): { netId: string; displayName: string } | null {
  try {
    const raw = window.sessionStorage.getItem(IDENTITY_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

interface PersistedMockState {
  scenario: MockScenario;
  intents: [string, MockIntent][];
  approvedIntentId: string | null;
  /** Drives UPI account rotation. Real implementation counts approved rows. */
  approvedCount: number;
}

function loadPersistedState(): PersistedMockState | null {
  try {
    const raw = window.sessionStorage.getItem(STATE_STORAGE_KEY);
    if (!raw) return null;
    const state = JSON.parse(raw) as PersistedMockState;
    // State belongs to the scenario that produced it. Switching scenario via
    // the query param must not inherit the previous one's purchase -- only
    // setMockScenario() clears the key, and the query param bypasses it.
    return state.scenario === getMockScenario() ? state : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Mock engine — dead-code-eliminated from production builds. Every function
// below is only ever reached when `ENDPOINT_MODE[x] === "mock"` AND
// `import.meta.env.DEV` is true; `vite build` statically resolves
// `import.meta.env.DEV` to `false` and strips the unreachable code that
// follows the early return.
// ---------------------------------------------------------------------------

const SINGLE_PRICE = 299;   // phase 3
const COUPLE_PRICE = 519;   // phase 3 -- NOT 2x single, never derive it
const SEATS_TOTAL = 600;    // real venue cap; ~550 sold offline, ~50 left
const HOLD_MINUTES = 20;

interface MockIntent {
  id: string;
  ticketType: TicketTypeCode;
  seats: number;
  amount: number;
  referenceCode: string;
  holdExpiresAt: number; // epoch ms
  state: PaymentState;
  reason?: string;
  resubmitCount: number;
  statusPollCount: number;
  payerName: string;
}

interface MockEngine {
  availability(): Promise<AvailabilityResponse>;
  createHold(body: HoldRequest): Promise<HoldResponse>;
  paymentPage(id: string): Promise<PaymentPageResponse>;
  requestProofUpload(id: string): Promise<ProofUrlResponse>;
  submitProof(id: string, body: SubmitProofRequest): Promise<SubmitProofResponse>;
  paymentStatus(id: string): Promise<PaymentStatusResponse>;
  myPass(): Promise<MyPassResponse>;
  adminListPayments(state?: string): Promise<AdminPaymentListResponse>;
  adminApprovePayment(id: string): Promise<void>;
  adminRejectPayment(id: string, body: AdminRejectRequest): Promise<void>;
}

function referenceCode(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let out = "";
  for (let i = 0; i < 6; i += 1) out += chars[Math.floor(Math.random() * chars.length)];
  return `UG-${out}`;
}

function buildEngine(): MockEngine | null {
  if (!import.meta.env.DEV) return null;

  const restored = loadPersistedState();
  const intents = new Map<string, MockIntent>(restored?.intents ?? []);
  let approvedIntentId: string | null = restored?.approvedIntentId ?? null;
  // Successful payments so far. Rotation is a pure function of this number, so
  // the account a buyer was shown is reproducible at reconciliation time.
  let approvedCount = restored?.approvedCount ?? 0;

  // `has_pass` drops you straight into an approved COUPLE purchase, so /pass has
  // something to show without walking the six-step flow. COUPLE rather than
  // SINGLE because two cards exercise the addendum A3 behaviour -- two tickets,
  // two QR codes, scanned independently.
  /** Seed a demo identity's existing purchase.
   *
   * Called at REQUEST time, not here at construction: the engine is built when
   * the module loads, which is before `/api/v1/me` has resolved, so the signed
   * -in identity is not known yet. Idempotent -- it returns immediately once a
   * purchase exists.
   */
  function ensureDemoPurchase(): void {
    if (approvedIntentId) return;
    const who = currentIdentity();
    if (!who) return;
    const bought = DEMO_PREPURCHASED[who.netId];
    if (!bought) return;
    const intent: MockIntent = {
      id: `demo-${who.netId}`,
      ticketType: bought.ticketType,
      seats: bought.seats,
      amount: bought.seats === 2 ? COUPLE_PRICE : SINGLE_PRICE,
      referenceCode: `UG-${who.netId.toUpperCase()}`,
      holdExpiresAt: Date.now(),
      state: "APPROVED",
      resubmitCount: 0,
      statusPollCount: 0,
      payerName: bought.holderName ?? who.displayName,
    };
    intents.set(intent.id, intent);
    approvedIntentId = intent.id;
  }

  if (!restored && getMockScenario() === "has_pass") {
    const demo: MockIntent = {
      id: "demo-approved-intent",
      ticketType: "COUPLE",
      seats: 2,
      amount: COUPLE_PRICE,
      referenceCode: "UG-DEMO01",
      holdExpiresAt: Date.now(),
      state: "APPROVED",
      resubmitCount: 0,
      statusPollCount: 0,
      payerName: currentIdentity()?.displayName ?? "Aarav Sharma",
    };
    intents.set(demo.id, demo);
    approvedIntentId = demo.id;
  }

  function persist(): void {
    try {
      window.sessionStorage.setItem(
        STATE_STORAGE_KEY,
        JSON.stringify({
          scenario: getMockScenario(),
          intents: [...intents.entries()],
          approvedIntentId,
          approvedCount,
        }),
      );
    } catch {
      // Demo convenience only -- a storage failure must never break the app.
    }
  }

  const seededQueue: AdminPaymentListItem[] = [
    {
      id: "seed-1",
      submitted_at: new Date(Date.now() - 12 * 60_000).toISOString(),
      payer_name: "Ishaan Mehta",
      amount: SINGLE_PRICE,
      utr: "312099887766",
      proof_url: "/mock/proof/seed-1.jpg",
      flags: [],
    },
    {
      id: "seed-2",
      submitted_at: new Date(Date.now() - 40 * 60_000).toISOString(),
      payer_name: "Diya Kapoor",
      amount: COUPLE_PRICE,
      utr: "312099881234",
      proof_url: "/mock/proof/seed-2.jpg",
      flags: [],
    },
    {
      id: "seed-3",
      submitted_at: new Date(Date.now() - 2 * 60_000).toISOString(),
      payer_name: "Rohan Nair",
      amount: SINGLE_PRICE,
      utr: "312099880001",
      proof_url: "/mock/proof/seed-3.jpg",
      flags: ["LATE_NO_SEAT"],
    },
  ];
  let adminQueue = [...seededQueue];

  function findLiveIntent(id: string): MockIntent {
    const intent = intents.get(id);
    if (!intent) {
      throw new ApiError(403, "HOLD_REQUIRED", "We couldn't find that reservation. Start again.", null, null);
    }
    if (intent.state === "HOLD" && Date.now() > intent.holdExpiresAt) {
      intent.state = "EXPIRED";
    }
    return intent;
  }

  const api: MockEngine = {
    async availability() {
      const scenario = getMockScenario();
      if (scenario === "sold_out") {
        return {
          seats_total: SEATS_TOTAL,
          seats_available: 0,
          types: [
            { code: "SINGLE", label: "Single", price: SINGLE_PRICE, seats: 1, available: false },
            { code: "COUPLE", label: "Couple", price: COUPLE_PRICE, seats: 2, available: false },
          ],
        };
      }
      if (scenario === "last_seat_couple_disabled") {
        return {
          seats_total: SEATS_TOTAL,
          seats_available: 1,
          types: [
            { code: "SINGLE", label: "Single", price: SINGLE_PRICE, seats: 1, available: true },
            { code: "COUPLE", label: "Couple", price: COUPLE_PRICE, seats: 2, available: false },
          ],
        };
      }
      // ~550 of the 600 were sold offline and arrive via sheet ingestion, so
      // phase 3 opens with roughly 50 on sale. seats_available is what is
      // still buyable; seats_total stays the venue cap.
      return {
        seats_total: SEATS_TOTAL,
        seats_available: 50,
        types: [
          { code: "SINGLE", label: "Single", price: SINGLE_PRICE, seats: 1, available: true },
          { code: "COUPLE", label: "Couple", price: COUPLE_PRICE, seats: 2, available: true },
        ],
      };
    },

    async createHold(body) {
      const scenario = getMockScenario();

      if (scenario === "sold_out") {
        throw new ApiError(409, "SOLD_OUT", "Sold out — no seats remain for this event.", null, null);
      }
      if (scenario === "last_seat_couple_disabled" && body.ticket_type === "COUPLE") {
        throw new ApiError(
          409,
          "TYPE_UNAVAILABLE",
          "Only 1 seat left — singles only.",
          null,
          null,
        );
      }

      const seats = body.ticket_type === "COUPLE" ? 2 : 1;
      const amount = body.ticket_type === "COUPLE" ? COUPLE_PRICE : SINGLE_PRICE;
      const holdMs = scenario === "hold_expiring" ? 8_000 : HOLD_MINUTES * 60_000;
      const id = crypto.randomUUID();
      const intent: MockIntent = {
        id,
        ticketType: body.ticket_type,
        seats,
        amount,
        referenceCode: referenceCode(),
        holdExpiresAt: Date.now() + holdMs,
        state: "HOLD",
        resubmitCount: 0,
        statusPollCount: 0,
        payerName: body.details.full_name,
      };
      intents.set(id, intent);

      return {
        intent_id: id,
        ticket_type: intent.ticketType,
        seats: intent.seats,
        amount: intent.amount,
        hold_expires_at: new Date(intent.holdExpiresAt).toISOString(),
        reference_code: intent.referenceCode,
      };
    },

    async paymentPage(id) {
      const intent = findLiveIntent(id);
      if (intent.state !== "HOLD") {
        throw new ApiError(
          403,
          "HOLD_EXPIRED",
          "Your reservation expired. Seats were released — start again to request a new hold.",
          null,
          null,
        );
      }
      // Server picks the account; the client renders whatever it is handed.
      const account = accountForApprovedCount(approvedCount);
      // Amount and reference go INSIDE the QR payload, not just the button, so
      // a buyer scanning from another device cannot mistype the amount.
      const upiDeepLink =
        `upi://pay?pa=${encodeURIComponent(account.vpa)}` +
        `&pn=${encodeURIComponent("SNUPHORIA")}` +
        `&am=${intent.amount}&cu=INR&tn=${encodeURIComponent(intent.referenceCode)}`;
      return {
        amount: intent.amount,
        upi_vpa: account.vpa,
        upi_qr_url: upiDeepLink,
        upi_deep_link: upiDeepLink,
        reference_code: intent.referenceCode,
        hold_expires_at: new Date(intent.holdExpiresAt).toISOString(),
      };
    },

    async requestProofUpload(id) {
      findLiveIntent(id);
      return {
        upload_url: `http://localhost:4443/mock-upload/${id}`,
        object_key: `payments/${id}/proof.jpg`,
      };
    },

    async submitProof(id, body) {
      const scenario = getMockScenario();
      const intent = findLiveIntent(id);

      if (scenario === "duplicate_utr" && intent.resubmitCount === 0) {
        intent.resubmitCount += 1;
        throw new ApiError(
          409,
          "UTR_ALREADY_USED",
          "That UTR has already been used for another payment. Double-check the number and try again.",
          null,
          null,
        );
      }

      if (scenario === "rejected_one_resubmit_left") {
        if (intent.state === "CLOSED" || intent.resubmitCount >= 2) {
          throw new ApiError(
            409,
            "RESUBMIT_LIMIT",
            "You've used your one resubmit for this payment. Contact the desk to resolve it.",
            null,
            null,
          );
        }
        if (intent.state === "REJECTED") {
          intent.resubmitCount += 1;
        }
      }

      intent.state = "PENDING_APPROVAL";
      intent.statusPollCount = 0;
      intent.payerName = body.payer_name;

      return { state: "PENDING_APPROVAL" as PaymentState };
    },

    async paymentStatus(id) {
      const scenario = getMockScenario();
      const intent = findLiveIntent(id);

      if (intent.state === "PENDING_APPROVAL") {
        intent.statusPollCount += 1;

        if (scenario === "rejected_one_resubmit_left") {
          if (intent.statusPollCount >= 2 && intent.resubmitCount === 0) {
            intent.state = "REJECTED";
            intent.reason =
              "The screenshot amount didn't match. You have one resubmit remaining.";
          } else if (intent.statusPollCount >= 2 && intent.resubmitCount === 1) {
            intent.state = "CLOSED";
            intent.reason =
              "Two rejections — this payment is closed. Contact the desk to resolve it manually.";
          }
        } else if (intent.statusPollCount >= 3) {
          intent.state = "APPROVED";
          approvedIntentId = intent.id;
          approvedCount += 1;
        }
      }

      return {
        state: intent.state,
        reason: intent.reason,
        flags: [],
      };
    },

    async myPass() {
      ensureDemoPurchase();
      if (!approvedIntentId) {
        return { tickets: [] };
      }
      const intent = intents.get(approvedIntentId);
      if (!intent || intent.state !== "APPROVED") {
        return { tickets: [] };
      }
      const tickets: PassTicket[] = [];
      for (let seatIndex = 1; seatIndex <= intent.seats; seatIndex += 1) {
        tickets.push({
          short_code: `${intent.referenceCode.replace("UG-", "")}${seatIndex}`,
          qr_url: `/mock/qr/${intent.id}-${seatIndex}.png`,
          holder_name: seatIndex === 1 ? intent.payerName : "Partner",
          seat_index: seatIndex,
          seats: intent.seats,
          state: "ISSUED",
        });
      }
      return { tickets };
    },

    async adminListPayments(state) {
      // All seeded/demo items are PENDING_APPROVAL; querying any other
      // state correctly returns an empty queue in this mock.
      if (state && state !== "PENDING_APPROVAL") {
        return { items: [] };
      }
      return { items: adminQueue };
    },

    async adminApprovePayment(id) {
      adminQueue = adminQueue.filter((item) => item.id !== id);
      approvedCount += 1;
    },

    async adminRejectPayment(id, body) {
      void body;
      adminQueue = adminQueue.filter((item) => item.id !== id);
    },
  };

  // Persist after any engine call rather than at each mutation site, so a new
  // method cannot silently forget to save.
  return new Proxy(api, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (typeof value !== "function") return value;
      return async (...args: unknown[]) => {
        const result = await (value as (...a: unknown[]) => unknown)(...args);
        persist();
        return result;
      };
    },
  });
}

const engine = buildEngine();

function requireEngine(): MockEngine {
  if (!engine) {
    throw new Error("Mock adapter is unavailable in a production build.");
  }
  return engine;
}

// ---------------------------------------------------------------------------
// Public per-endpoint functions — what screens actually import. Each one
// dispatches through ENDPOINT_MODE.
// ---------------------------------------------------------------------------

export function getAvailability(): Promise<AvailabilityResponse> {
  return ENDPOINT_MODE.availability === "real"
    ? api.get<AvailabilityResponse>("/api/v1/availability")
    : requireEngine().availability();
}

export function createHold(body: HoldRequest): Promise<HoldResponse> {
  return ENDPOINT_MODE.createHold === "real"
    ? api.post<HoldResponse>("/api/v1/payments/hold", body)
    : requireEngine().createHold(body);
}

export function getPaymentPage(intentId: string): Promise<PaymentPageResponse> {
  return ENDPOINT_MODE.paymentPage === "real"
    ? api.get<PaymentPageResponse>(`/api/v1/payments/${intentId}/page`)
    : requireEngine().paymentPage(intentId);
}

export function requestProofUpload(intentId: string): Promise<ProofUrlResponse> {
  return ENDPOINT_MODE.requestProofUpload === "real"
    ? api.post<ProofUrlResponse>(`/api/v1/payments/${intentId}/proof-url`)
    : requireEngine().requestProofUpload(intentId);
}

export function submitProof(intentId: string, body: SubmitProofRequest): Promise<SubmitProofResponse> {
  return ENDPOINT_MODE.submitProof === "real"
    ? api.post<SubmitProofResponse>(`/api/v1/payments/${intentId}/submit`, body)
    : requireEngine().submitProof(intentId, body);
}

export function getPaymentStatus(intentId: string): Promise<PaymentStatusResponse> {
  return ENDPOINT_MODE.paymentStatus === "real"
    ? api.get<PaymentStatusResponse>(`/api/v1/payments/${intentId}/status`)
    : requireEngine().paymentStatus(intentId);
}

export function getMyPass(): Promise<MyPassResponse> {
  return ENDPOINT_MODE.myPass === "real"
    ? api.get<MyPassResponse>("/my/pass")
    : requireEngine().myPass();
}

export function adminListPayments(state = "PENDING_APPROVAL"): Promise<AdminPaymentListResponse> {
  return ENDPOINT_MODE.adminListPayments === "real"
    ? api.get<AdminPaymentListResponse>(`/api/v1/admin/payments?state=${encodeURIComponent(state)}`)
    : requireEngine().adminListPayments(state);
}

export function adminApprovePayment(intentId: string, force = false): Promise<void> {
  return ENDPOINT_MODE.adminApprovePayment === "real"
    ? api.post<void>(`/api/v1/admin/payments/${intentId}/approve${force ? "?force=true" : ""}`)
    : requireEngine().adminApprovePayment(intentId);
}

export function adminRejectPayment(intentId: string, body: AdminRejectRequest): Promise<void> {
  return ENDPOINT_MODE.adminRejectPayment === "real"
    ? api.post<void>(`/api/v1/admin/payments/${intentId}/reject`, body)
    : requireEngine().adminRejectPayment(intentId, body);
}
