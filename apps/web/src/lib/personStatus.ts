/**
 * Resolves what the signed-in person already holds, so screens can branch on
 * fact rather than role. Roles cannot make this distinction: a sheet-ingested
 * holder is a VISITOR until their ticket is found, and an OWNER can be holding
 * a ticket like anybody else.
 *
 * Selling is closed and the booking flow has moved to `feature/booking-flow`,
 * so this no longer tracks payment intents. There are three answers now, and
 * they come from two real endpoints rather than a mock adapter:
 *
 *   has_pass   — a pass exists; show it
 *   needs_id   — a ticket exists but the ID check is not finished
 *   none       — this account holds nothing
 */
import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { getMyPass } from "./passApi";
import { useSession } from "./session";
import type { MyVerification, PassTicket } from "./types";
import { getMyVerification } from "./verificationApi";

export type PersonStatus =
  | { kind: "loading" }
  | { kind: "has_pass"; tickets: PassTicket[] }
  | { kind: "needs_id"; verification: MyVerification }
  | { kind: "none" };

export function usePersonStatus(): PersonStatus {
  const { user, loading: sessionLoading } = useSession();
  const [status, setStatus] = useState<PersonStatus>({ kind: "loading" });

  useEffect(() => {
    // Wait for GET /api/v1/me to settle before deciding anything — resolving
    // "none" early would flash the wrong call to action for a second, the
    // exact class of bug this hook exists to fix.
    if (sessionLoading) return;

    if (!user) {
      setStatus({ kind: "none" });
      return;
    }

    let cancelled = false;
    setStatus({ kind: "loading" });

    async function resolve() {
      // The verification endpoint is the one that knows whether a ticket
      // exists at all, so it is asked first. A 404 there means this account
      // holds nothing, which is an answer rather than a failure.
      let verification: MyVerification | null = null;
      try {
        verification = await getMyVerification();
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) {
          // Anything other than "no ticket" leaves us unsure; say nothing
          // rather than claim they hold nothing.
          if (!cancelled) setStatus({ kind: "none" });
          return;
        }
        if (!cancelled) setStatus({ kind: "none" });
        return;
      }
      if (cancelled) return;

      try {
        const pass = await getMyPass();
        if (cancelled) return;
        const usable = pass.tickets.filter((ticket) => ticket.qr_token);
        if (usable.length > 0) {
          setStatus({ kind: "has_pass", tickets: pass.tickets });
          return;
        }
      } catch {
        // A ticket exists even if the pass call failed — fall through to the
        // ID step rather than telling them they hold nothing.
      }
      if (!cancelled) setStatus({ kind: "needs_id", verification });
    }

    void resolve();
    return () => {
      cancelled = true;
    };
  }, [user, sessionLoading]);

  return status;
}
