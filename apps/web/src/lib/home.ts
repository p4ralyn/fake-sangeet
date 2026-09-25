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
 *   /gate   SECURITY work the door. Their screen is the room's counts and a
 *           way into the scanner — no rail, and nothing that names an attendee
 *           (LLD-v1 §11.5). They still walk the participant flow for their OWN
 *           pass, since they attend too and their ID is checked like anybody's,
 *           so the header carries a link rather than forcing a redirect.
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

/** The screen this person should land on after signing in.
 *
 * Ops staff outrank gate staff deliberately: an ADMIN who is also on the door
 * still wants the full dashboard, and can reach /gate from there.
 */
export function homeFor(roles: readonly Role[]): string {
  if (isOpsStaff(roles)) return "/ops";
  if (isGateStaff(roles)) return "/gate";
  return "/pass";
}
