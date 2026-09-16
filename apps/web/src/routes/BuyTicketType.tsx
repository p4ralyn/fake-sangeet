import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Stepper } from "../components/Stepper";
import { formatRupees } from "../lib/format";
import { getAvailability } from "../lib/mock";
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
 */
export function BuyTicketType() {
  const navigate = useNavigate();
  const [availability, setAvailability] = useState<AvailabilityResponse | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    getAvailability().then((result) => {
      if (!cancelled) setAvailability(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

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
