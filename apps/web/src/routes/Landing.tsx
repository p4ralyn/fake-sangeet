import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Sparkle } from "../components/Sparkle";
import { ZariRule } from "../components/ZariRule";
import { EVENT_DATE_LABEL, EVENT_NAME_FALLBACK, EVENT_VENUE } from "../lib/event";
import { getAvailability } from "../lib/mock";
import { usePersonStatus } from "../lib/personStatus";
import { useSession } from "../lib/session";
import type { AvailabilityResponse } from "../lib/types";
import styles from "./Landing.module.css";

const POLL_MS = 15_000;

/**
 * Invitation (`/`) — the ceremonial landing. See
 * docs/design/participant-screens.md §1. Availability is polled every 15s;
 * while the very first fetch is in flight we show nothing rather than a
 * stale or zeroed count.
 *
 * The primary action branches on what THIS person already holds
 * (usePersonStatus — myPass, then any remembered in-progress intent), never
 * on availability alone and never on role. A pass holder must see "View your
 * pass", not an invitation to buy a second ticket the server would refuse.
 */
export function Landing() {
  const { user, signIn } = useSession();
  const navigate = useNavigate();
  const personStatus = usePersonStatus();
  const [availability, setAvailability] = useState<AvailabilityResponse | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const result = await getAvailability();
        if (!cancelled) setAvailability(result);
      } catch {
        // Availability is a display cache, never load-bearing for safety —
        // a failed poll just leaves the last known value on screen.
      }
    }

    void poll();
    const interval = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const soldOut = availability?.seats_available === 0;
  const singlesOnly = availability?.seats_available === 1;

  function handleReserve() {
    if (soldOut) {
      navigate("/waitlist");
      return;
    }
    if (!user) {
      signIn("/buy");
      return;
    }
    navigate("/buy");
  }

  return (
    <Card className={styles.hero}>
      <ZariRule />
      <p className={styles.invitedTo}>
        <Sparkle />
        You&rsquo;re invited
        <Sparkle />
      </p>
      <h1 className={styles.eventName}>{user?.event.name ?? EVENT_NAME_FALLBACK}</h1>
      <p className={styles.dateVenue}>
        {EVENT_DATE_LABEL}
        <br />
        {EVENT_VENUE}
      </p>
      <ZariRule />

      <div className={styles.action}>
        {personStatus.kind === "loading" ? (
          // Reserves the same space a real button takes so nothing shifts
          // once we know which one to show — and, critically, never shows
          // "Reserve your seat" to someone we haven't yet confirmed doesn't
          // already hold a pass.
          <Button variant="primary" disabled>
            Checking&hellip;
          </Button>
        ) : personStatus.kind === "has_pass" ? (
          <Button variant="primary" onClick={() => navigate("/pass")}>
            View your pass
          </Button>
        ) : personStatus.kind === "in_progress" ? (
          <Button variant="primary" onClick={() => navigate(personStatus.target)}>
            {personStatus.state === "HOLD" ? "Continue to payment" : "View payment status"}
          </Button>
        ) : (
          <>
            <Button variant="primary" onClick={handleReserve}>
              {soldOut ? "Join the waitlist" : "Reserve your seat"}
            </Button>
            {availability && (
              <p className={styles.availabilityLine}>
                {soldOut ? (
                  <>All {availability.seats_total} seats are taken.</>
                ) : singlesOnly ? (
                  <>Only 1 seat left — singles only</>
                ) : (
                  <>
                    <span className="tabular-nums">{availability.seats_available}</span> of{" "}
                    <span className="tabular-nums">{availability.seats_total}</span> seats left
                  </>
                )}
              </p>
            )}
          </>
        )}
      </div>

      {(personStatus.kind === "none" || personStatus.kind === "loading") && (
        <Link className={styles.passLink} to="/pass">
          Already have a pass?
        </Link>
      )}
    </Card>
  );
}
