import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { Card } from "../components/Card";
import { homeFor, isOpsStaff } from "../lib/home";
import { usePersonStatus } from "../lib/personStatus";
import { useSession } from "../lib/session";

export interface ParticipantRouteProps {
  /** Send somebody who has not finished their ID check to /verify first.
   * Set on /pass and nowhere else — /verify IS the ID check, so gating it on
   * itself would loop. */
  requireIdCheck?: boolean;
  children: ReactNode;
}

/**
 * The participant side of the door, for people who are actually walking it.
 *
 * Two redirects, both of which exist because the alternative is a dead end:
 *
 *  - **Ops staff never see these screens.** An OWNER or ADMIN holds no ticket
 *    in that capacity, so the pass screen would say "no pass on this account"
 *    and the ID upload would have nothing to attach a photo to. They go to
 *    their dashboard instead.
 *  - **The ID check comes before the pass.** The QR is the thing that opens a
 *    gate, and nobody should reach it before a human has something to check
 *    them against. The server already refuses to mint a token early; this
 *    makes the screen agree with it rather than showing an empty QR slot.
 *
 * SECURITY is deliberately NOT redirected. They attend the event too, so they
 * walk this exact flow for their own pass; their door tool is a link in the
 * header (see AppShell).
 */
export function ParticipantRoute({ requireIdCheck = false, children }: ParticipantRouteProps) {
  const { roles, loading } = useSession();
  const personStatus = usePersonStatus();

  if (loading) {
    return (
      <Card>
        <p>Loading&hellip;</p>
      </Card>
    );
  }

  if (isOpsStaff(roles)) {
    return <Navigate to={homeFor(roles)} replace />;
  }

  if (requireIdCheck) {
    if (personStatus.kind === "loading") {
      return (
        <Card>
          <p>Loading&hellip;</p>
        </Card>
      );
    }
    // Only "needs_id" is redirected. "none" falls through so /pass can say
    // this account holds nothing, which is a different and truthful answer.
    if (personStatus.kind === "needs_id") {
      return <Navigate to="/verify" replace />;
    }
  }

  return <>{children}</>;
}
