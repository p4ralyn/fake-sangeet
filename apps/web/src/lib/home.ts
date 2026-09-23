/**
 * Where a person belongs, derived from the roles the SERVER put in the
 * session — never a client-side flag, and never a hardcoded list repeated at
 * each call site (CLAUDE.md, "derive gates dynamically").
 *
 * Three answers, because the three cohorts want three different first screens:
 *
 *   /ops    OWNER and ADMIN run the event. They hold no ticket qua staff, so
 *           the pass screen and the ID upload are dead ends for them — both
 *           redirect here rather than showing a page with nothing on it.
 *   /scan   SECURITY work the door. They still walk the participant flow for
 *           their OWN pass (they attend too, and their ID is checked like
 *           anybody's), so this is a LINK, not a redirect — the one tool they
 *           get, and deliberately not the rest of /ops (LLD-v1 §11.5).
 *   /pass   everybody else.
 */
import type { Role } from "./types";

/** Roles that run the event: the whole /ops area, no participant screens. */
export const OPS_ROLES: readonly Role[] = ["OWNER", "ADMIN"];

/** The gate crew. /scan and nothing else on the staff side. */
export const GATE_ROLES: readonly Role[] = ["SECURITY"];

export function isOpsStaff(roles: readonly Role[]): boolean {
  return roles.some((role) => OPS_ROLES.includes(role));
}

export function isGateStaff(roles: readonly Role[]): boolean {
  return roles.some((role) => GATE_ROLES.includes(role));
}

/** The screen this person should land on after signing in. */
export function homeFor(roles: readonly Role[]): string {
  return isOpsStaff(roles) ? "/ops" : "/pass";
}
