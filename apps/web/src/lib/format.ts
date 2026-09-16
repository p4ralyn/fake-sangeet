/**
 * Small formatting helpers shared by the participant screens. Additive —
 * does not touch any existing module's public surface.
 */

/** Whole-rupee amounts only (see LLD-v2 §4) — no paise anywhere in this app. */
export function formatRupees(amount: number): string {
  return `₹${amount.toLocaleString("en-IN")}`;
}

/** `2026-11-14T21:14:00Z` -> `21:14`, in the viewer's local time. */
export function formatClockTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
}
