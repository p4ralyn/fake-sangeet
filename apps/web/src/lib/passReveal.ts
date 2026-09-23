/**
 * One piece of per-device memory: whether this person has already watched the
 * pass reveal animation. Never load-bearing — the server is the source of
 * truth for everything that matters, and a cleared browser just means somebody
 * sees a nice animation twice.
 *
 * The hold and payment-intent helpers that used to live here moved to
 * `feature/booking-flow` with the rest of the buy flow.
 */

const PASS_REVEAL_KEY = "unigate_pass_revealed";

export function hasSeenPassReveal(): boolean {
  try {
    return window.localStorage.getItem(PASS_REVEAL_KEY) === "1";
  } catch {
    // Private mode or blocked storage: play the reveal. Harmless either way.
    return false;
  }
}

export function markPassRevealSeen(): void {
  try {
    window.localStorage.setItem(PASS_REVEAL_KEY, "1");
  } catch {
    // Best-effort.
  }
}
