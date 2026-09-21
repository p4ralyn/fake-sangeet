import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { hasSeenPassReveal, markPassRevealSeen } from "../lib/holdStore";
import { getMyPass } from "../lib/mock";
import type { PassTicket, TicketState } from "../lib/types";
import styles from "./Pass.module.css";

const STATE_COPY: Record<TicketState, { label: string; glyph: string; className: string }> = {
  ISSUED: { label: "Not arrived", glyph: "○", className: styles.stateNeutral },
  ID_PENDING: { label: "ID check pending", glyph: "○", className: styles.stateNeutral },
  ID_VERIFIED: { label: "Not arrived", glyph: "○", className: styles.stateNeutral },
  MANUAL_REVIEW: { label: "ID under manual review", glyph: "◑", className: styles.stateWarn },
  CHECKED_IN: { label: "Inside", glyph: "●", className: styles.stateGood },
  EXIT_BREAK: { label: "On a break", glyph: "◑", className: styles.stateWarn },
  CHECKED_OUT: { label: "Departed", glyph: "✓", className: styles.stateNeutral },
  REVOKED: { label: "Revoked", glyph: "✕", className: styles.stateBad },
};

function PassCard({ ticket, animate }: { ticket: PassTicket; animate: boolean }) {
  const ticketTypeLabel = ticket.seats === 2 ? "Couple" : "Single";
  const state = STATE_COPY[ticket.state];

  return (
    <div className={styles.wrapper}>
      <div className={`${styles.passCard} ${animate ? styles.revealing : ""}`}>
        {animate && (
          <>
            <span className={styles.edgeTop} aria-hidden="true" />
            <span className={styles.edgeRight} aria-hidden="true" />
            <span className={styles.edgeBottom} aria-hidden="true" />
            <span className={styles.edgeLeft} aria-hidden="true" />
          </>
        )}

        <div className={styles.passTop}>
          <p className={styles.holderName}>{ticket.holder_name}</p>
          <p className={styles.ticketType}>{ticketTypeLabel}</p>
          {ticket.seats > 1 && (
            <p className={styles.seatOf}>
              Seat {ticket.seat_index} of {ticket.seats}
            </p>
          )}
        </div>

        <div className={styles.perforation} aria-hidden="true" />

        <div className={styles.passStub}>
          <div className={`${styles.qrWrap} ${animate ? styles.qrResolving : ""}`}>
            <QRCodeSVG value={ticket.qr_url} size={140} bgColor="#FFF8ED" fgColor="#241009" level="M" />
          </div>
          <p className={`${styles.shortCode} tabular-nums`}>{ticket.short_code}</p>
        </div>
      </div>

      <p className={`${styles.stateLine} ${state.className}`}>
        <span aria-hidden="true">{state.glyph}</span> {state.label}
      </p>
    </div>
  );
}

/**
 * Your pass (`/pass`) — the one bold element in the product. A couple
 * renders two cards, because it is two tickets, two QR codes, counted
 * separately at the gate. The reveal plays once, on first view, and never
 * under prefers-reduced-motion: reduce (enforced globally in base.css).
 */
export function Pass() {
  const navigate = useNavigate();
  const [tickets, setTickets] = useState<PassTicket[] | null>(null);
  const [animate] = useState(() => !hasSeenPassReveal());

  useEffect(() => {
    let cancelled = false;
    getMyPass().then((result) => {
      if (!cancelled) setTickets(result.tickets);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (tickets && tickets.length > 0) {
      markPassRevealSeen();
    }
  }, [tickets]);

  if (!tickets) {
    return (
      <Card>
        <p>Loading your pass&hellip;</p>
      </Card>
    );
  }

  if (tickets.length === 0) {
    return (
      <Card>
        <h1>Your pass</h1>
        <p>No pass yet. Reserve a seat and complete payment to get one.</p>
        <Button variant="primary" onClick={() => navigate("/buy")}>
          Reserve your seat
        </Button>
      </Card>
    );
  }

  return (
    <div className={styles.page}>
      <h1>Your pass</h1>
      {tickets.map((ticket) => (
        <PassCard key={ticket.short_code} ticket={ticket} animate={animate} />
      ))}
      <Card className={styles.addToHomeCard}>
        <p className={styles.addToHomeTitle}>Add to home screen</p>
        <p>
          On your phone, open your browser&rsquo;s menu and choose &ldquo;Add to home screen&rdquo; so your
          pass is one tap away at the door.
        </p>
      </Card>
    </div>
  );
}
