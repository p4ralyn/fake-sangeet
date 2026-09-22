import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { ApiError } from "../lib/api";
import { uploadIdImage, useMyVerification } from "../lib/verificationApi";
import type { MyVerification } from "../lib/types";
import styles from "./VerifyId.module.css";

/**
 * Cohort A's one job after signing in: photograph a student ID so a human can
 * check it (docs/design/cohort-and-verification.md).
 *
 * Everything the screen offers is gated on the server's `can_upload`, never on
 * the frontend's own reading of a ticket status. There is exactly one source of
 * truth about whether this person may send a photo, and it is not in this file.
 *
 * `capture="environment"` opens the back camera straight into the camera app on
 * a phone, which is where every one of these photos will actually be taken. On
 * a laptop the same input is an ordinary file picker.
 */

/** Bumped alongside `Settings.id_consent_version` when this wording changes. */
const CONSENT_TEXT =
  "I agree that my ID photo is used to check me in at the door, and is deleted 45 days after the event.";

export function VerifyId() {
  const state = useMyVerification();

  if (state.kind === "loading") {
    return (
      <Card>
        <p>Checking your ticket…</p>
      </Card>
    );
  }

  if (state.kind === "none") {
    return (
      <Card>
        <h1>No ticket on this account</h1>
        <p>
          We can’t find a ticket for the account you signed in with. If you bought one, it may
          be under a different email — sign in with your SNU account, or talk to the desk.
        </p>
        <p>
          <Link to="/">Back to the invitation</Link>
        </p>
      </Card>
    );
  }

  if (state.kind === "error") {
    return (
      <Card>
        <h1>Couldn’t load your status</h1>
        <p>{state.message}</p>
        <button type="button" className={styles.retry} onClick={state.reload}>
          Try again
        </button>
      </Card>
    );
  }

  return <VerifyCard verification={state.verification} onChange={state.reload} />;
}

function VerifyCard({
  verification,
  onChange,
}: {
  verification: MyVerification;
  onChange: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const done = verification.ticket_status === "PASS_ISSUED" || verification.ticket_status === "VERIFIED";
  // When the status line already explains why the upload is closed, the
  // server's `blocked_reason` would only say it a second time in different
  // words. Two sentences telling someone the same thing reads as a glitch.
  const statusExplains =
    verification.outcome === "MANUAL_REVIEW" || verification.outcome === "REJECTED";

  async function send() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadIdImage(file, { consent: consented });
      setFile(null);
      setPreview(null);
      setConsented(false);
      onChange();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "That didn’t send. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h1>Your student ID</h1>
      <Status verification={verification} />

      {done ? (
        <p className={styles.next}>
          <Link to="/pass">Go to your pass</Link>
        </p>
      ) : verification.can_upload ? (
        <>
          <p className={styles.guidance}>
            Take a photo of your student ID with the whole card in frame, flat and in good
            light. Someone checks it by hand, so it only has to be readable — it doesn’t have
            to be perfect.
          </p>

          <input
            ref={inputRef}
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            className={styles.file}
            onChange={(event) => {
              const chosen = event.target.files?.[0] ?? null;
              setFile(chosen);
              setError(null);
              // Shown so they can see what they are about to send. Revoked on
              // replacement so a long session doesn't leak object URLs.
              setPreview((old) => {
                if (old) URL.revokeObjectURL(old);
                return chosen ? URL.createObjectURL(chosen) : null;
              });
            }}
          />
          <button
            type="button"
            className={styles.choose}
            onClick={() => inputRef.current?.click()}
          >
            {file ? "Take a different photo" : "Take a photo of your ID"}
          </button>

          {preview && <img className={styles.preview} src={preview} alt="The photo you chose" />}

          <label className={styles.consent}>
            <input
              type="checkbox"
              checked={consented}
              onChange={(event) => setConsented(event.target.checked)}
            />
            <span>{CONSENT_TEXT}</span>
          </label>

          {error && <p className={styles.error}>{error}</p>}

          <button
            type="button"
            className={styles.submit}
            disabled={!file || !consented || busy}
            onClick={() => void send()}
          >
            {busy ? "Sending…" : "Send for checking"}
          </button>
          <p className={styles.attempts}>
            {verification.attempts_remaining} of{" "}
            {verification.attempts_remaining + verification.attempts_used} tries left
          </p>
        </>
      ) : statusExplains ? null : (
        <p className={styles.blocked}>{verification.blocked_reason}</p>
      )}
    </Card>
  );
}

/** One line saying where this person stands, and the reviewer's note when
 * there is one to act on. */
function Status({ verification }: { verification: MyVerification }) {
  if (verification.ticket_status === "PASS_ISSUED" || verification.ticket_status === "VERIFIED") {
    return <p className={styles.good}>Checked and approved. Your pass is ready.</p>;
  }
  if (verification.outcome === "MANUAL_REVIEW") {
    return (
      <p className={styles.waiting}>
        Your photo is with the team. You’ll get an email when it’s been looked at.
      </p>
    );
  }
  if (verification.outcome === "RETRY_REQUIRED") {
    return (
      <p className={styles.retryNote}>
        They need a clearer photo.
        {verification.review_note ? ` They said: “${verification.review_note}”` : ""}
      </p>
    );
  }
  if (verification.outcome === "REJECTED") {
    return (
      <p className={styles.refused}>
        Your ID wasn’t accepted.
        {verification.review_note ? ` They said: “${verification.review_note}”` : ""} Talk to the
        desk if you think that’s wrong.
      </p>
    );
  }
  return (
    <p className={styles.guidance}>
      You’re on the list. One more step before your pass: a photo of your student ID.
    </p>
  );
}
