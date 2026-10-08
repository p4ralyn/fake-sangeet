import { useState } from "react";
import { Button, Field, Notice, Paper } from "../components";
import { ApiError } from "../lib/api";
import { transferPartnerSeat, transferPass } from "../lib/passApi";
import type { TransferResponse, TransferWindow } from "../lib/types";
import styles from "./TransferPanel.module.css";

/**
 * Moving a ticket off this account — two ways, one form.
 *
 * - `own`: give your pass to somebody else. For a PROXY couple whose partner
 *   seat is still unsent, both passes go together.
 * - `partner`: send a PROXY couple's locked partner seat to the partner. Only
 *   rendered when the server says there is such a seat.
 *
 * Deliberately two steps. Transfer is irreversible from this side — the moment
 * it goes through, the ticket belongs to a NetID the person typed from memory.
 * A single tap next to a text field is not enough friction for that, so the
 * confirm step repeats the NetID back and says plainly what is about to happen.
 *
 * The deadline is the server's. This screen reads `window.open` rather than
 * comparing clocks itself: a phone with a wrong clock must not be able to talk
 * itself into a transfer the API will refuse, or out of one it would allow.
 */

function formatDeadline(iso: string): string {
  const when = new Date(iso);
  return when.toLocaleString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

export interface TransferPanelProps {
  window: TransferWindow | null;
  onTransferred: (toNetId: string) => void;
  mode?: "own" | "partner";
  /** Own mode only: an unsent partner seat travels with this ticket. */
  carriesPartner?: boolean;
}

const SEND: Record<"own" | "partner", (netId: string) => Promise<TransferResponse>> = {
  own: transferPass,
  partner: transferPartnerSeat,
};

export function TransferPanel({
  window: transferWindow,
  onTransferred,
  mode = "own",
  carriesPartner = false,
}: TransferPanelProps) {
  const [netId, setNetId] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const partner = mode === "partner";
  const fieldId = partner ? "partner-net-id" : "transfer-net-id";

  if (!transferWindow) {
    return null;
  }

  if (!partner && transferWindow.transfers_used >= transferWindow.max_transfers) {
    return (
      <Paper as="section" className={styles.panel}>
        <h2 className={styles.title}>This pass can&rsquo;t be passed on again</h2>
        <p className={styles.note}>
          A pass can only be transferred once, and this one already has been. If something
          needs sorting out, talk to the desk.
        </p>
      </Paper>
    );
  }

  if (!transferWindow.open) {
    return (
      <Paper as="section" className={styles.panel}>
        <h2 className={styles.title}>Transfers are closed</h2>
        <p className={styles.note}>
          Passes stopped moving at {formatDeadline(transferWindow.closes_at)}, three hours
          before doors.{" "}
          {partner
            ? "Your partner’s pass can no longer be sent."
            : "Talk to the desk if something needs sorting out."}
        </p>
      </Paper>
    );
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const result = await SEND[mode](netId.trim());
      onTransferred(result.to_net_id);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "That didn’t go through. Check your connection and try again.",
      );
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Paper as="section" lift={partner ? "md" : "none"} className={styles.panel}>
      {partner ? (
        <>
          <h2 className={styles.title}>
            Bought an extra ticket for your partner? Transfer it to them now.
          </h2>
          <p className={styles.note}>
            They sign in with their own SNU account and upload their own ID to get the QR.
            <strong> Once it&rsquo;s theirs, it&rsquo;s theirs</strong> &mdash; they can pass
            it on once if they can&rsquo;t make it. Transfers close at{" "}
            {formatDeadline(transferWindow.closes_at)}.
          </p>
        </>
      ) : (
        <>
          <h2 className={styles.title}>Give this pass to someone else</h2>
          <p className={styles.note}>
            <strong>A pass can only be transferred once.</strong> Once you send this, it
            can&rsquo;t be passed on again &mdash; not by you, and not by them.
          </p>
          {carriesPartner && (
            <p className={styles.note}>
              Your partner&rsquo;s pass goes with it &mdash; both passes move to them, and
              they can still send the second one on to their partner.
            </p>
          )}
          <p className={styles.note}>
            They sign in with their own SNU account and upload their own ID. Your QR stops
            working straight away. Transfers close at{" "}
            {formatDeadline(transferWindow.closes_at)}.
          </p>
        </>
      )}

      {!confirming ? (
        <>
          <Field
            id={fieldId}
            label={partner ? "Your partner’s NetID" : "Their NetID"}
            inputSize="lg"
            value={netId}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="zq202"
            error={error}
            onChange={(event) => {
              setNetId(event.target.value);
              setError(null);
            }}
          />
          <Button
            variant="primary"
            block
            className={styles.action}
            disabled={netId.trim().length < 3}
            onClick={() => setConfirming(true)}
          >
            Continue
          </Button>
        </>
      ) : (
        <>
          <Notice tone="warn" className={styles.confirm}>
            <p>
              Send {partner ? "your partner’s" : "this"} pass to{" "}
              <strong>{netId.trim().toLowerCase()}</strong>? You can&rsquo;t undo this, and it
              can&rsquo;t be transferred again after this.
            </p>
          </Notice>
          {error && (
            <Notice tone="error" live>
              <p>{error}</p>
            </Notice>
          )}
          <div className={styles.confirmRow}>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(false)}>
              Go back
            </Button>
            <Button variant="danger" disabled={busy} onClick={() => void send()}>
              {busy ? "Sending…" : "Yes, send it"}
            </Button>
          </div>
        </>
      )}
    </Paper>
  );
}
