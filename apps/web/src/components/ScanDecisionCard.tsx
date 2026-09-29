import { useEffect, useState } from "react";
import type { ScanAction, SecurityScanResponse } from "../lib/types";
import { Button } from "./Button/Button";
import { Stamp, type StampTone } from "./Stamp/Stamp";
import styles from "./ScanDecisionCard.module.css";

/**
 * The full-screen card a volunteer reads before admitting someone
 * (LLD-v1 §11.5).
 *
 * Two deviations from §11.5's sketch, both for a phone held in one hand at a
 * door rather than the desktop layout the sketch implies:
 *
 * 1. The ID photo is at the TOP and the actions at the BOTTOM, not side by
 *    side. The operator's eyes go up to compare a face; their thumb stays
 *    down. Mixing the two means reaching across the screen mid-decision.
 * 2. There is no `[DENY]` button, because the server never offers `DENIED`
 *    in `legal_actions` — the state machine has no transition for it. Not
 *    admitting someone is the *absence* of a confirmation, so the way out is
 *    "Don't admit", which closes the card and records nothing. A button that
 *    posted `DENIED` would fail every time it was pressed.
 *
 * The buttons come from `legal_actions` in the server's order, always. The
 * phone never decides what is legal.
 */

/** Plain-language labels. The operator reads these, not the enum. */
const ACTION_LABELS: Record<ScanAction, string> = {
  CHECK_IN: "Check in",
  EXIT_BREAK: "Out on a break",
  RE_ENTRY: "Back in",
  CHECK_OUT: "Check out",
  DENIED: "Refuse",
  MANUAL_OVERRIDE: "Override",
};

/** How the pass's current state reads to a person. */
const STATE_LOOK: Record<SecurityScanResponse["state"], { tone: StampTone; glyph: string }> = {
  NOT_ARRIVED: { tone: "neutral", glyph: "○" },
  INSIDE: { tone: "good", glyph: "●" },
  ON_BREAK: { tone: "warn", glyph: "◑" },
  DEPARTED: { tone: "neutral", glyph: "✓" },
  REVOKED: { tone: "bad", glyph: "✕" },
};

const STATE_LABELS: Record<SecurityScanResponse["state"], string> = {
  NOT_ARRIVED: "Not arrived yet",
  INSIDE: "Inside",
  ON_BREAK: "On a break",
  DEPARTED: "Checked out",
  REVOKED: "Revoked",
};

export interface ScanDecisionCardProps {
  response: SecurityScanResponse;
  busy: boolean;
  onConfirm: (action: ScanAction) => void;
  /** Close without recording anything — the decision token simply expires. */
  onDismiss: () => void;
  /** Fired when the decision token dies under the operator's hands. */
  onExpire: () => void;
}

export function ScanDecisionCard({ response, busy, onConfirm, onDismiss, onExpire }: ScanDecisionCardProps) {
  const seconds = useExpiryCountdown(response.decision_expires_in, onExpire);

  const total = response.decision_expires_in;
  const look = STATE_LOOK[response.state];

  return (
    <section className={styles.card} aria-label="Confirm this person">
      <div className={styles.photoFrame}>
        {response.id_image_url ? (
          <img className={styles.photo} src={response.id_image_url} alt="Uploaded ID card" />
        ) : (
          <p className={styles.photoMissing}>
            No ID photo on file. Check their student ID by hand before admitting.
          </p>
        )}
      </div>

      <div className={styles.identity}>
        <h1 className={styles.name}>{response.participant_name ?? "Name not on file"}</h1>
        <div className={styles.facts}>
          <Stamp tone={look.tone} glyph={look.glyph} className={styles.state}>
            {STATE_LABELS[response.state]}
          </Stamp>
          {response.max_breaks > 0 && (
            <p className={styles.breaks}>
              {response.breaks_used} of {response.max_breaks} breaks used
            </p>
          )}
        </div>
      </div>

      <div className={styles.actions}>
        {response.legal_actions.map((action) => (
          <Button
            key={action}
            variant={action === "DENIED" ? "danger" : "good"}
            size="lg"
            block
            className={styles.action}
            disabled={busy}
            onClick={() => onConfirm(action)}
          >
            {ACTION_LABELS[action]}
          </Button>
        ))}
        <Button variant="secondary" size="lg" block className={styles.dismiss} disabled={busy} onClick={onDismiss}>
          Don&rsquo;t admit
        </Button>
        {seconds !== null && (
          <div className={styles.expiryRow}>
            {total ? (
              <span className={styles.expiryTrack} aria-hidden="true">
                <span
                  className={styles.expiryFill}
                  style={{ transform: `scaleX(${Math.max(0, seconds) / total})` }}
                />
              </span>
            ) : null}
            <p className={styles.expiry} role="timer" aria-live="off">
              This decision expires in {seconds}s
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function useExpiryCountdown(initial: number | null, onExpire: () => void): number | null {
  const [seconds, setSeconds] = useState<number | null>(initial);

  useEffect(() => {
    setSeconds(initial);
    if (initial === null) {
      return;
    }
    const deadline = Date.now() + initial * 1000;
    let fired = false;
    const interval = window.setInterval(() => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setSeconds(left);
      if (left === 0 && !fired) {
        fired = true;
        window.clearInterval(interval);
        onExpire();
      }
    }, 1000);
    return () => window.clearInterval(interval);
    // `onExpire` is intentionally not a dependency: re-running this on every
    // parent render would restart the countdown on a card the operator is
    // mid-way through reading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  return seconds;
}
