import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Field } from "../components/Field";
import { getAvailability } from "../lib/mock";
import { useSession } from "../lib/session";
import type { TicketTypeCode } from "../lib/types";
import styles from "./Waitlist.module.css";

/**
 * Waitlist (`/waitlist`) — honest and short. NOTE: the frontend API contract
 * (docs/design/frontend-api-contract.md) does not define a waitlist
 * endpoint — it isn't in the "real today" or "mocked until M5" lists. This
 * submits locally only (no network call) until a real endpoint exists; wire
 * it up then and delete this comment.
 */
export function Waitlist() {
  const { user } = useSession();
  const [seatsTotal, setSeatsTotal] = useState<number | null>(null);
  const [name, setName] = useState(user?.display_name ?? "");
  const [netId, setNetId] = useState(user?.net_id ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [ticketType, setTicketType] = useState<TicketTypeCode>("SINGLE");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAvailability().then((result) => {
      if (!cancelled) setSeatsTotal(result.seats_total);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    await new Promise((resolve) => window.setTimeout(resolve, 400));
    setSubmitting(false);
    setSubmitted(true);
  }

  return (
    <Card>
      <h1>Waitlist</h1>
      {submitted ? (
        <p>You&rsquo;re on the list. We&rsquo;ll email you if a seat opens up.</p>
      ) : (
        <>
          <p>
            {seatsTotal ? `All ${seatsTotal} seats are taken.` : "All seats are taken."} Leave your details and
            we&rsquo;ll email you if one opens up.
          </p>
          <form onSubmit={handleSubmit} noValidate>
            <Field label="Full name" value={name} onChange={(e) => setName(e.target.value)} required />
            <Field label="NetID" value={netId} onChange={(e) => setNetId(e.target.value)} required />
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />

            <fieldset className={styles.ticketFieldset}>
              <legend className={styles.legend}>Ticket type you wanted</legend>
              <label className={styles.radioOption}>
                <input
                  type="radio"
                  name="ticketType"
                  value="SINGLE"
                  checked={ticketType === "SINGLE"}
                  onChange={() => setTicketType("SINGLE")}
                />
                <span>Single</span>
              </label>
              <label className={styles.radioOption}>
                <input
                  type="radio"
                  name="ticketType"
                  value="COUPLE"
                  checked={ticketType === "COUPLE"}
                  onChange={() => setTicketType("COUPLE")}
                />
                <span>Couple</span>
              </label>
            </fieldset>

            <Button type="submit" variant="primary" disabled={submitting}>
              {submitting ? "Submitting…" : "Join the waitlist"}
            </Button>
          </form>
        </>
      )}
    </Card>
  );
}
