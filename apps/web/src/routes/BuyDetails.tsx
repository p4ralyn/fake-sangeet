import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Field } from "../components/Field";
import { Stepper } from "../components/Stepper";
import { ZariRule } from "../components/ZariRule";
import { ApiError } from "../lib/api";
import { rememberHoldDetails } from "../lib/holdStore";
import { createHold } from "../lib/mock";
import { useSession } from "../lib/session";
import type { RegistrationDetails, TicketTypeCode } from "../lib/types";
import styles from "./BuyDetails.module.css";

const STEPS = ["Choose", "Details", "Payment"];

/**
 * Your details (`/buy/details`) — step 2 of 3. No payment information
 * appears on this screen; that ordering is the point (addendum §A2). A hold
 * is granted only after this form is submitted.
 */
export function BuyDetails() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useSession();

  const ticketType = searchParams.get("type") as TicketTypeCode | null;
  const isCouple = ticketType === "COUPLE";

  const [fullName, setFullName] = useState(user?.display_name ?? "");
  const [netId, setNetId] = useState(user?.net_id ?? "");
  const [phone, setPhone] = useState("");
  const [department, setDepartment] = useState("");
  const [year, setYear] = useState("");
  const [partnerName, setPartnerName] = useState("");
  const [partnerEmail, setPartnerEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [soldOut, setSoldOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (ticketType !== "SINGLE" && ticketType !== "COUPLE") {
      navigate("/buy", { replace: true });
    }
  }, [ticketType, navigate]);

  if (ticketType !== "SINGLE" && ticketType !== "COUPLE") {
    return null;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    const details: RegistrationDetails = {
      full_name: fullName,
      net_id: netId,
      phone,
      department,
      year,
      consent,
      ...(isCouple ? { partner: { mode: "SELF_CLAIM" as const, partner_name: partnerName, partner_email: partnerEmail } } : {}),
    };

    try {
      const hold = await createHold({ ticket_type: ticketType as TicketTypeCode, details });
      rememberHoldDetails(hold.intent_id, { payerName: fullName, payerPhone: phone });
      navigate(`/buy/pay/${hold.intent_id}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "SOLD_OUT") {
        setSoldOut(true);
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <Stepper steps={STEPS} currentIndex={1} />
      <h1>Your details</h1>

      {soldOut ? (
        <div className={styles.soldOut}>
          <p>Those seats went while you were filling this in. Nothing has been charged.</p>
          <Button variant="primary" onClick={() => navigate("/waitlist")}>
            Join the waitlist
          </Button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} noValidate>
          <Field
            label="Full name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            required
            autoComplete="name"
          />
          <Field label="NetID" value={netId} onChange={(e) => setNetId(e.target.value)} required autoComplete="username" />
          <Field
            label="Phone"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            required
            autoComplete="tel"
          />
          <Field label="Department" value={department} onChange={(e) => setDepartment(e.target.value)} required />
          <Field label="Year" value={year} onChange={(e) => setYear(e.target.value)} required />

          {isCouple && (
            <>
              <ZariRule />
              <p className={styles.partnerNote}>
                Your partner signs in with their own university account, uploads their own ID, and gets their
                own QR code. We&rsquo;ll email them a link.
              </p>
              <Field
                label="Partner's name"
                value={partnerName}
                onChange={(e) => setPartnerName(e.target.value)}
                required
              />
              <Field
                label="Partner's email"
                type="email"
                value={partnerEmail}
                onChange={(e) => setPartnerEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </>
          )}

          <ZariRule />

          <label className={styles.consent}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              required
            />
            <span>I agree to the privacy notice and to my ID card being checked for entry.</span>
          </label>

          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" disabled={!consent || submitting}>
            {submitting ? "Reserving…" : "Reserve your seat"}
          </Button>
        </form>
      )}
    </Card>
  );
}
