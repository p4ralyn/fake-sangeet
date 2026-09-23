import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Sparkle } from "../components/Sparkle";
import { ZariRule } from "../components/ZariRule";
import { hasSeenPassReveal, markPassRevealSeen } from "../lib/passReveal";
import { getMyPass } from "../lib/passApi";
import { TransferPanel } from "./TransferPanel";
import type { MyPassResponse, PassTicket, TicketState } from "../lib/types";
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
        <ZariRule orientation="vertical" thin className={styles.railLeft} />
        <ZariRule orientation="vertical" thin className={styles.railRight} />

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

        <ZariRule thin />

        <div className={styles.passStub}>
          <div className={`${styles.qrWrap} ${animate ? styles.qrResolving : ""}`}>
            {ticket.qr_token ? (
              <QRCodeSVG
                value={ticket.qr_token}
                size={140}
                bgColor="#FFF3DC"
                fgColor="#5E0C18"
                level="M"
              />
            ) : (
              // A ticket whose ID check hasn't cleared has no pass and so no
              // token. Showing a QR-shaped placeholder would be worse than
              // saying so: somebody would try to scan it at the door.
              <p className={styles.qrPending}>Your QR appears once your ID is checked.</p>
            )}
          </div>
          <p className={`${styles.shortCode} tabular-nums`}>{ticket.short_code || "—"}</p>
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
  const [pass, setPass] = useState<MyPassResponse | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [animate] = useState(() => !hasSeenPassReveal());
  const [transferredTo, setTransferredTo] = useState<string | null>(null);
  const tickets = pass?.tickets ?? null;

  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    getMyPass()
      .then((result) => {
        if (!cancelled) setPass(result);
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [retryCount]);

  useEffect(() => {
    if (tickets && tickets.length > 0) {
      markPassRevealSeen();
    }
  }, [tickets]);

  if (transferredTo) {
    // Shown instead of the pass, not underneath it: the QR above is dead the
    // moment the transfer lands, and a live-looking QR next to "sent" is a
    // person trying to scan it at a door.
    return (
      <Card>
        <h1>Sent to {transferredTo}</h1>
        <p>
          This pass isn&rsquo;t yours any more and the QR on this phone has stopped
          working. {transferredTo} signs in with their own SNU account and uploads their
          own ID before they get a pass.
        </p>
      </Card>
    );
  }

  if (loadFailed) {
    return (
      <Card>
        <h1>Your pass</h1>
        <p>We couldn&rsquo;t reach the server. Please try again.</p>
        <Button variant="primary" onClick={() => setRetryCount((n) => n + 1)}>
          Try again
        </Button>
      </Card>
    );
  }

  if (!tickets) {
    return (
      <Card>
        <p>Loading your pass&hellip;</p>
      </Card>
    );
  }

  if (tickets.length === 0) {
    // Selling is closed and this branch no longer has a buy flow, so the only
    // honest thing to say is "we can't find you" plus where to go next.
    return (
      <Card>
        <h1>No pass on this account</h1>
        <p>
          We can&rsquo;t find a ticket for the account you signed in with. If you have one,
          it may be under a different SNU account &mdash; or ask at the desk.
        </p>
      </Card>
    );
  }

  return (
    <div className={styles.page}>
      <h1 className={styles.pageTitle}>
        <Sparkle />
        Your pass
      </h1>
      {tickets.map((ticket) => (
        <PassCard
          // short_code is empty until a pass is issued, so it cannot be the key
          // on its own — a transferred ticket has none yet.
          key={`${ticket.seat_index}-${ticket.short_code}`}
          ticket={ticket}
          animate={animate}
        />
      ))}

      {tickets.some((ticket) => ticket.state !== "REVOKED") && (
        <TransferPanel
          window={pass?.transfer ?? null}
          onTransferred={setTransferredTo}
        />
      )}
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
