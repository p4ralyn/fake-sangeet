/**
 * Resolves what the signed-in person already holds, so screens can branch on
 * fact rather than role. Roles cannot make this distinction: a sheet-ingested
 * or already-approved holder is PARTICIPANT in production, but in the
 * current mock a demo pre-purchased identity (e.g. zq204) is still VISITOR,
 * so role-based branching gives the wrong answer today and would silently
 * rot once M3 lands. This is why the landing page kept offering "Reserve
 * your seat" to someone who already had a pass.
 *
 * "In progress" covers every state that still counts as this person's one
 * live intent — HOLD, PENDING_APPROVAL, REJECTED (addendum §A1/§A2: HOLD +
 * PENDING_APPROVAL + APPROVED all count against the seat cap, and
 * pi_one_live_per_user allows only one live intent per person). A terminal
 * state (EXPIRED, CANCELLED, CLOSED) or an intent the server no longer
 * recognises clears the remembered id and resolves to "none" — the person is
 * free to start over.
 */
import { useEffect, useState } from "react";
import { clearCurrentIntent, getCurrentIntentId } from "./holdStore";
import { getMyPass, getPaymentStatus } from "./mock";
import { useSession } from "./session";
import type { PassTicket, PaymentState } from "./types";

export type PersonStatus =
  | { kind: "loading" }
  | { kind: "has_pass"; tickets: PassTicket[] }
  | { kind: "in_progress"; intentId: string; state: PaymentState; target: string }
  | { kind: "none" };

/** Where the primary action should send this person, for a live intent. */
function targetFor(intentId: string, state: PaymentState): string | null {
  if (state === "HOLD") return `/buy/pay/${intentId}`;
  if (state === "PENDING_APPROVAL" || state === "REJECTED") return `/status/${intentId}`;
  return null;
}

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
    const netId = user.net_id;

    async function resolve() {
      try {
        const pass = await getMyPass();
        if (cancelled) return;
        if (pass.tickets.length > 0) {
          clearCurrentIntent(netId);
          setStatus({ kind: "has_pass", tickets: pass.tickets });
          return;
        }
      } catch {
        // myPass failing isn't fatal here — fall through and check for an
        // in-progress intent, same as "no pass yet".
      }
      if (cancelled) return;

      const intentId = getCurrentIntentId(netId);
      if (!intentId) {
        setStatus({ kind: "none" });
        return;
      }

      try {
        const result = await getPaymentStatus(intentId);
        if (cancelled) return;
        const target = targetFor(intentId, result.state);
        if (target) {
          setStatus({ kind: "in_progress", intentId, state: result.state, target });
        } else {
          clearCurrentIntent(netId);
          setStatus({ kind: "none" });
        }
      } catch {
        // The intent is gone or unreachable (e.g. HOLD_REQUIRED) — nothing
        // to resume.
        clearCurrentIntent(netId);
        if (!cancelled) setStatus({ kind: "none" });
      }
    }

    void resolve();
    return () => {
      cancelled = true;
    };
  }, [user, sessionLoading]);

  return status;
}
