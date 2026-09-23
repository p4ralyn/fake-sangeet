import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Sparkle } from "../components/Sparkle";
import { ZariRule } from "../components/ZariRule";
import { EVENT_DATE_LABEL, EVENT_NAME_FALLBACK, EVENT_VENUE } from "../lib/event";
import { homeFor, isOpsStaff } from "../lib/home";
import { usePersonStatus } from "../lib/personStatus";
import { useSession } from "../lib/session";
import styles from "./Landing.module.css";

/**
 * Invitation (`/`) — the ceremonial landing. See
 * docs/design/participant-screens.md §1.
 *
 * **The event is sold out and selling is closed.** There is no availability
 * poll and no "Reserve your seat" here any more; the buy flow lives on
 * `feature/booking-flow` for whenever phase-3 selling comes back. What is left
 * is the door: do you hold a pass, and is your ID checked.
 *
 * The primary action branches on what THIS person actually holds, never on
 * role — a pass holder must see "View your pass", and somebody mid-ID-check
 * must be sent to finish it rather than shown a dead end.
 */
export function Landing() {
  const { user, roles, signIn } = useSession();
  const navigate = useNavigate();
  const personStatus = usePersonStatus();

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
        {!user ? (
          <>
            <Button variant="primary" onClick={() => signIn("/pass")}>
              Sign in to see your pass
            </Button>
            <p className={styles.availabilityLine}>
              Use the SNU account your ticket was bought with.
            </p>
          </>
        ) : isOpsStaff(roles) ? (
          // Staff who run the event hold no ticket in that capacity. Asking
          // them to "view your pass" would send them to a screen that
          // redirects — so say what actually happens.
          <Button variant="primary" onClick={() => navigate(homeFor(roles))}>
            Open the dashboard
          </Button>
        ) : personStatus.kind === "loading" ? (
          // Reserves the same space a real button takes so nothing shifts once
          // we know which one to show.
          <Button variant="primary" disabled>
            Checking&hellip;
          </Button>
        ) : personStatus.kind === "has_pass" ? (
          <Button variant="primary" onClick={() => navigate("/pass")}>
            View your pass
          </Button>
        ) : personStatus.kind === "needs_id" ? (
          <>
            <Button variant="primary" onClick={() => navigate("/verify")}>
              {personStatus.verification.outcome === "MANUAL_REVIEW"
                ? "Check your ID status"
                : "Verify your ID"}
            </Button>
            <p className={styles.availabilityLine}>
              You&rsquo;re on the list. One step left before your pass.
            </p>
          </>
        ) : (
          <>
            <Button variant="primary" onClick={() => navigate("/pass")}>
              View your pass
            </Button>
            <p className={styles.availabilityLine}>
              Sold out. If you have a ticket it&rsquo;s under your SNU account.
            </p>
          </>
        )}
      </div>

    </Card>
  );
}
