/**
 * Small client-side memory for values the server doesn't hand back but that
 * make later steps of the same flow nicer — never load-bearing for
 * correctness (the server is always the source of truth for the hold
 * itself; see GET /api/v1/payments/{id}/page, which is re-fetched on every
 * visit to the payment screen so a reload never fabricates a second hold).
 */

interface HoldDetails {
  payerName: string;
  payerPhone: string;
}

const DETAILS_PREFIX = "unigate_hold_details:";
const PASS_REVEAL_KEY = "unigate_pass_revealed";
const CURRENT_INTENT_PREFIX = "unigate_current_intent:";

export function rememberHoldDetails(intentId: string, details: HoldDetails): void {
  try {
    window.sessionStorage.setItem(`${DETAILS_PREFIX}${intentId}`, JSON.stringify(details));
  } catch {
    // sessionStorage can be unavailable (private browsing); prefill is a
    // convenience, not a requirement, so fail silently.
  }
}

export function getHoldDetails(intentId: string): HoldDetails | null {
  try {
    const raw = window.sessionStorage.getItem(`${DETAILS_PREFIX}${intentId}`);
    return raw ? (JSON.parse(raw) as HoldDetails) : null;
  } catch {
    return null;
  }
}

/** The pass reveal motion plays once ever, never again after first view. */
export function hasSeenPassReveal(): boolean {
  try {
    return window.localStorage.getItem(PASS_REVEAL_KEY) === "1";
  } catch {
    return true;
  }
}

export function markPassRevealSeen(): void {
  try {
    window.localStorage.setItem(PASS_REVEAL_KEY, "1");
  } catch {
    // Best-effort — worst case the motion plays again on a future visit.
  }
}

/**
 * Remembers which payment intent belongs to which signed-in NetID, so a
 * screen that only knows "the signed-in person" (Landing, the buy flow's
 * entry points) can find their in-flight intent without a
 * "GET my current intent" endpoint, which does not exist in the API
 * contract. Scoped by NetID — not just one shared key — because the dev
 * identity picker lets a tester switch identities inside one browser
 * session, and an unscoped key would leak one person's intent onto the next
 * signed-in identity's landing page.
 *
 * Never load-bearing for correctness: the server remains the source of
 * truth for every state check (see personStatus.ts), this only tells a
 * screen which intent id to ask about.
 */
export function rememberCurrentIntent(netId: string, intentId: string): void {
  try {
    window.sessionStorage.setItem(`${CURRENT_INTENT_PREFIX}${netId.toLowerCase()}`, intentId);
  } catch {
    // Demo/UX convenience only; never break the app over storage.
  }
}

export function getCurrentIntentId(netId: string): string | null {
  try {
    return window.sessionStorage.getItem(`${CURRENT_INTENT_PREFIX}${netId.toLowerCase()}`);
  } catch {
    return null;
  }
}

export function clearCurrentIntent(netId: string): void {
  try {
    window.sessionStorage.removeItem(`${CURRENT_INTENT_PREFIX}${netId.toLowerCase()}`);
  } catch {
    // no-op
  }
}
