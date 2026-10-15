import { useEffect, useState } from "react";
import { QRCodeCanvas } from "qrcode.react";
import { Link } from "react-router-dom";
import {
  Button,
  DisplayTitle,
  EmptyState,
  FilmStrip,
  GuestTag,
  Loading,
  Notice,
  Paper,
  Sparkle,
  Stamp,
  cx,
  type StampTone,
} from "../components";
import { hasSeenPassReveal, markPassRevealSeen } from "../lib/passReveal";
import { getMyPass } from "../lib/passApi";
import { TransferPanel } from "./TransferPanel";
import type { MyPassResponse, PassTicket, TicketState } from "../lib/types";
import styles from "./Pass.module.css";

const STATE_COPY: Record<TicketState, { label: string; glyph: string; tone: StampTone }> = {
  ISSUED: { label: "Not arrived", glyph: "○", tone: "neutral" },
  ID_PENDING: { label: "ID check pending", glyph: "○", tone: "neutral" },
  ID_VERIFIED: { label: "Not arrived", glyph: "○", tone: "neutral" },
  MANUAL_REVIEW: { label: "ID under manual review", glyph: "◑", tone: "warn" },
  CHECKED_IN: { label: "Inside", glyph: "●", tone: "good" },
  EXIT_BREAK: { label: "On a break", glyph: "◑", tone: "warn" },
  CHECKED_OUT: { label: "Departed", glyph: "✓", tone: "neutral" },
  REVOKED: { label: "Revoked", glyph: "✕", tone: "bad" },
};

/**
 * One ticket, as a length of film: sprockets top and bottom, the holder and
 * the QR in the frame, a perforation, and the stub with the short code and
 * the state stamped on it.
 */
/** Shown in place of the QR on a partner seat nobody has been sent yet. */
const LOCKED = { label: "Not sent yet", glyph: "◌", tone: "neutral" as StampTone };

function PassCard({ ticket, animate }: { ticket: PassTicket; animate: boolean }) {
  const ticketTypeLabel = ticket.seats === 2 ? "Couple" : "Single";
  const state = ticket.locked ? LOCKED : STATE_COPY[ticket.state];

  return (
    <div className={styles.wrapper}>
      <article
        className={cx(styles.passCard, animate && styles.revealing, ticket.locked && styles.locked)}
      >
        <FilmStrip size="thin" className={styles.sprockets} />

        <div className={styles.passTop}>
          <p className={styles.holderName}>
            {ticket.locked ? "Your partner’s pass" : ticket.holder_name}
          </p>
          <p className={styles.ticketType}>
            <span className={styles.typeChip}>{ticketTypeLabel}</span>
            {ticket.seats > 1 && (
              <span className={styles.seatOf}>
                Seat {ticket.seat_index} of {ticket.seats}
              </span>
            )}
          </p>
          <GuestTag tag={ticket.guest_tag} size="sm" className={styles.side} />
        </div>

        <div className={cx(styles.qrWrap, animate && styles.qrResolving)}>
          <span className={cx(styles.frameCorner, styles.tl)} aria-hidden="true" />
          <span className={cx(styles.frameCorner, styles.tr)} aria-hidden="true" />
          <span className={cx(styles.frameCorner, styles.bl)} aria-hidden="true" />
          <span className={cx(styles.frameCorner, styles.br)} aria-hidden="true" />
          {ticket.locked ? (
            // Nobody's ID stands behind this seat yet, so there is no QR to
            // show — only the way to give it to the person it is for.
            <div className={styles.lockedFace}>
              <LockGlyph />
              <p className={styles.lockedText}>Locked until you send it to your partner</p>
            </div>
          ) : ticket.qr_token ? (
            // Pure black on pure white, drawn to a canvas. Forced dark modes
            // (Chrome's "darken websites", Samsung Internet, Smart Invert)
            // recolour an inline SVG — maroon-on-cream came out pink and
            // would not scan at the door. Pixels on a canvas are left alone.
            <QRCodeCanvas
              className={styles.qr}
              value={ticket.qr_token}
              size={464}
              bgColor="#FFFFFF"
              fgColor="#000000"
              level="M"
              includeMargin
            />
          ) : (
            // A ticket whose ID check hasn't cleared has no pass and so no
            // token. Showing a QR-shaped placeholder would be worse than
            // saying so: somebody would try to scan it at the door.
            <p className={styles.qrPending}>Your QR appears once your ID is checked.</p>
          )}
        </div>

        <div className={styles.perforation} aria-hidden="true" />

        <div className={styles.passStub}>
          <p className={cx(styles.shortCode, "tabular-nums")}>{ticket.short_code || "—"}</p>
          <Stamp tone={state.tone} glyph={state.glyph} animate={animate} className={styles.stateStamp}>
            {state.label}
          </Stamp>
        </div>

        <FilmStrip size="thin" className={styles.sprockets} />
      </article>
    </div>
  );
}

function LockGlyph() {
  return (
    <svg className={styles.lockGlyph} viewBox="0 0 64 72" aria-hidden="true" focusable="false">
      <path
        d="M16 32V22a16 16 0 0 1 32 0v10"
        fill="none"
        stroke="#4a0a14"
        strokeWidth="7"
        strokeLinecap="round"
      />
      <rect x="6" y="30" width="52" height="38" rx="6" fill="#c8102e" stroke="#4a0a14" strokeWidth="4" />
      <circle cx="32" cy="46" r="5.5" fill="#ffc61a" stroke="#4a0a14" strokeWidth="2" />
      <rect x="29.5" y="49" width="5" height="10" rx="2" fill="#4a0a14" />
    </svg>
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
  const [partnerSentTo, setPartnerSentTo] = useState<string | null>(null);
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
      <EmptyState title={<>Sent to {transferredTo}</>}>
        <p>
          This pass isn&rsquo;t yours any more and the QR on this phone has stopped
          working. {transferredTo} signs in with their own SNU account and uploads their
          own ID before they get a pass.
        </p>
      </EmptyState>
    );
  }

  if (loadFailed) {
    return (
      <EmptyState
        title="Your pass"
        action={
          <Button variant="primary" onClick={() => setRetryCount((n) => n + 1)}>
            Try again
          </Button>
        }
      >
        <p>We couldn&rsquo;t reach the server. Please try again.</p>
      </EmptyState>
    );
  }

  if (!tickets) {
    return <Loading>Loading your pass&hellip;</Loading>;
  }

  if (tickets.length === 0) {
    // Selling is closed and this branch no longer has a buy flow, so the only
    // honest thing to say is "we can't find you" plus where to go next.
    return (
      <EmptyState title="No pass on this account">
        <p>
          We can&rsquo;t find a ticket for the account you signed in with. If you have one,
          it may be under a different SNU account &mdash; or ask at the desk.
        </p>
      </EmptyState>
    );
  }

  return (
    <div className={styles.page}>
      <DisplayTitle tone="ink" className={styles.pageTitle}>
        <Sparkle className={styles.titleSparkle} />
        Your pass
      </DisplayTitle>
      {tickets.map((ticket) => (
        <PassCard
          // short_code is empty until a pass is issued, so it cannot be the key
          // on its own — a transferred ticket has none yet.
          key={`${ticket.seat_index}-${ticket.short_code}`}
          ticket={ticket}
          animate={animate}
        />
      ))}

      {partnerSentTo && (
        <Notice tone="good" live>
          <p>
            Sent to <strong>{partnerSentTo}</strong>. They sign in with their own SNU account
            and upload their own ID to get their QR.
          </p>
        </Notice>
      )}

      {pass?.partner_transfer && (
        <TransferPanel
          mode="partner"
          window={pass.partner_transfer}
          onTransferred={(to) => {
            // The seat has left this account: show what is left, which is one pass.
            setPartnerSentTo(to);
            setRetryCount((n) => n + 1);
          }}
        />
      )}

      {tickets.some((ticket) => ticket.state !== "REVOKED") && (
        <TransferPanel
          carriesPartner={tickets.some((ticket) => ticket.locked)}
          window={pass?.transfer ?? null}
          onTransferred={setTransferredTo}
        />
      )}
      <Paper as="aside" pad="sm" className={styles.addToHomeCard}>
        <svg className={styles.phoneIcon} viewBox="0 0 32 48" aria-hidden="true" focusable="false">
          <rect x="2" y="2" width="28" height="44" rx="5" fill="#fff3dc" stroke="#4a0a14" strokeWidth="3" />
          <rect x="8" y="10" width="7" height="7" rx="1.5" fill="#c8102e" />
          <rect x="17" y="10" width="7" height="7" rx="1.5" fill="#e0147c" />
          <rect x="8" y="19" width="7" height="7" rx="1.5" fill="#ffc61a" stroke="#4a0a14" />
          <circle cx="16" cy="40" r="2" fill="#4a0a14" />
        </svg>
        <div>
          <p className={styles.addToHomeTitle}>Add to home screen</p>
          <p>
            On your phone, open your browser&rsquo;s menu and choose &ldquo;Add to home screen&rdquo; so your
            pass is one tap away at the door.{" "}
            <Link to="/tutorial#home">Step-by-step for iPhone and Android</Link>, and how the door,
            your one break and transfers work.
          </p>
        </div>
      </Paper>
    </div>
  );
}
