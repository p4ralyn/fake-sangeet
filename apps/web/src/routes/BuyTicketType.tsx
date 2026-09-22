import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Stepper } from "../components/Stepper";
import { formatRupees } from "../lib/format";
import { getAvailability } from "../lib/mock";
import { usePersonStatus } from "../lib/personStatus";
import type { AvailabilityResponse, TicketTypeAvailability } from "../lib/types";
import styles from "./BuyTicketType.module.css";

const STEPS = ["Choose", "Details", "Payment"];

const SEATS_LABEL: Record<string, string> = {
  SINGLE: "1 seat",
  COUPLE: "2 seats",
};

const COUPLE_CONSEQUENCE =
  "Two tickets, two QR codes. You and your partner are scanned in separately and can arrive or leave separately.";

/**
 * Choose your ticket (`/buy`) — step 1 of 3 (addendum §A2). A type is
 * offered only while seats_available >= type.seats; when unavailable it is
 * disabled and explained in place, never hidden, never clickable-then-rejected.
 *
 * One ticket per identity (`tickets_one_per_identity`) and one live intent
 * per person (`pi_one_live_per_user`) are both enforced server-side, so this
 * screen must not render the chooser for someone who already holds a pass or
 * has a purchase in progress — it routes them onward instead.
 */
export function BuyTicketType() {
  const navigate = useNavigate();
  const personStatus = usePersonStatus();
  const [availability, setAvailability] = useState<AvailabilityResponse | undefined>(undefined);
  const [loadFailed, setLoadFailed] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    getAvailability()
      .then((result) => {
        if (!cancelled) setAvailability(result);
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [retryCount]);

  useEffect(() => {
    if (personStatus.kind === "has_pass") {
      navigate("/pass", { replace: true });
    } else if (personStatus.kind === "in_progress") {
      navigate(personStatus.target, { replace: true });
    }
  }, [personStatus, navigate]);

  if (
    personStatus.kind === "loading" ||
    personStatus.kind === "has_pass" ||
    personStatus.kind === "in_progress"
  ) {
    return (
      <Card>
        <Stepper steps={STEPS} currentIndex={0} />
        <p>Checking availability&hellip;</p>
      </Card>
    );
  }

  if (loadFailed) {
    return (
      <Card>
        <Stepper steps={STEPS} currentIndex={0} />
        <h1>Choose your ticket</h1>
        <p>We couldn&rsquo;t reach the server. Please try again.</p>
        <Button variant="primary" onClick={() => setRetryCount((n) => n + 1)}>
          Try again
        </Button>
      </Card>
    );
  }

  if (!availability) {
    return (
      <Card>
        <Stepper steps={STEPS} currentIndex={0} />
        <p>Checking availability&hellip;</p>
      </Card>
    );
  }

  if (availability.seats_available === 0) {
    return (
      <Card>
        <Stepper steps={STEPS} currentIndex={0} />
        <h1>Choose your ticket</h1>
        <p>All {availability.seats_total} seats are taken.</p>
        <Button variant="primary" onClick={() => navigate("/waitlist")}>
          Join the waitlist
        </Button>
      </Card>
    );
  }

  function noteFor(type: TicketTypeAvailability): string | null {
    if (!type.available) {
      return availability!.seats_available === 1 ? "Only 1 seat left — singles only" : "Not enough seats left.";
    }
    if (type.code === "COUPLE") {
      return COUPLE_CONSEQUENCE;
    }
    return null;
  }

  return (
    <Card>
      <Stepper steps={STEPS} currentIndex={0} />
      <h1>Choose your ticket</h1>
      <div className={styles.options}>
        {availability.types.map((type) => {
          const note = noteFor(type);
          return (
            <div className={styles.option} key={type.code}>
              <button
                type="button"
                className={styles.optionButton}
                disabled={!type.available}
                aria-describedby={note ? `${type.code}-note` : undefined}
                onClick={() => navigate(`/buy/details?type=${type.code}`)}
              >
                <span className={styles.optionLabel}>{type.label}</span>
                <span className={`${styles.optionPrice} tabular-nums`}>{formatRupees(type.price)}</span>
                <span className={styles.optionSeats}>{SEATS_LABEL[type.code] ?? `${type.seats} seats`}</span>
              </button>
              {note && (
                <p id={`${type.code}-note`} className={type.available ? styles.consequence : styles.disabledReason}>
                  {note}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
