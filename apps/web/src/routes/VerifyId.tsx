import { useRef, useState } from "react";
import { Button, ButtonLink, Checkbox, EmptyState, Loading, Notice, Paper, PhotoPicker } from "../components";
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
    return <Loading>Checking your ticket…</Loading>;
  }

  if (state.kind === "none") {
    return (
      <EmptyState
        title="No ticket on this account"
        action={
          <ButtonLink to="/" variant="secondary">
            Back to the invitation
          </ButtonLink>
        }
      >
        <p>
          We can’t find a ticket for the account you signed in with. If you bought one, it may
          be under a different email — sign in with your SNU account, or talk to the desk.
        </p>
      </EmptyState>
    );
  }

  if (state.kind === "error") {
    return (
      <EmptyState
        title="Couldn’t load your status"
        action={
          <Button variant="primary" onClick={state.reload}>
            Try again
          </Button>
        }
      >
        <p>{state.message}</p>
      </EmptyState>
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
    <Paper as="section" pad="lg" tape className={styles.card}>
      <h1>Your student ID</h1>
      <Status verification={verification} />

      {done ? (
        <p className={styles.next}>
          <ButtonLink to="/pass" variant="primary" size="lg" block>
            Go to your pass
          </ButtonLink>
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
              setPreview((old) => {
                if (old) URL.revokeObjectURL(old);
                return chosen ? URL.createObjectURL(chosen) : null;
              });
            }}
          />
          <PhotoPicker
            preview={preview}
            previewAlt="The photo you chose"
            action={
              <Button
                variant={file ? "secondary" : "marigold"}
                block
                onClick={() => inputRef.current?.click()}
              >
                <CameraGlyph />
                {file ? "Take a different photo" : "Take a photo of your ID"}
              </Button>
            }
          />

          <Checkbox
            className={styles.consent}
            checked={consented}
            onChange={(event) => setConsented(event.target.checked)}
          >
            {CONSENT_TEXT}
          </Checkbox>

          {error && (
            <Notice tone="error" live>
              <p>{error}</p>
            </Notice>
          )}

          <Button
            variant="primary"
            size="lg"
            block
            className={styles.submit}
            disabled={!file || !consented || busy}
            onClick={() => void send()}
          >
            {busy ? "Sending…" : "Send for checking"}
          </Button>
          <p className={styles.attempts}>
            <span className={styles.pips} aria-hidden="true">
              {Array.from(
                { length: verification.attempts_remaining + verification.attempts_used },
                (_, i) => (
                  <span key={i} className={i < verification.attempts_remaining ? styles.pipOn : styles.pip} />
                ),
              )}
            </span>
            {verification.attempts_remaining} of{" "}
            {verification.attempts_remaining + verification.attempts_used} tries left
          </p>
        </>
      ) : statusExplains ? null : (
        <Notice tone="wait" className={styles.blocked}>
          <p>{verification.blocked_reason}</p>
        </Notice>
      )}
    </Paper>
  );
}

/** A small camera, drawn in the button's own colour. */
function CameraGlyph() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M4 7h3l2-3h6l2 3h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="13" r="3.6" fill="none" stroke="currentColor" strokeWidth="2.2" />
    </svg>
  );
}

function Status({ verification }: { verification: MyVerification }) {
  if (verification.ticket_status === "PASS_ISSUED" || verification.ticket_status === "VERIFIED") {
    return (
      <Notice tone="good">
        <p>Checked and approved. Your pass is ready.</p>
      </Notice>
    );
  }
  if (verification.outcome === "MANUAL_REVIEW") {
    return (
      <Notice tone="wait">
        <p>Your photo is with the team. You’ll get an email when it’s been looked at.</p>
      </Notice>
    );
  }
  if (verification.outcome === "RETRY_REQUIRED") {
    return (
      <Notice tone="warn">
        <p>
          They need a clearer photo.
          {verification.review_note ? ` They said: “${verification.review_note}”` : ""}
        </p>
      </Notice>
    );
  }
  if (verification.outcome === "REJECTED") {
    return (
      <Notice tone="error">
        <p>
          Your ID wasn’t accepted.
          {verification.review_note ? ` They said: “${verification.review_note}”` : ""} Talk to the
          desk if you think that’s wrong.
        </p>
      </Notice>
    );
  }
  return (
    <Notice tone="info">
      <p>You’re on the list. One more step before your pass: a photo of your student ID.</p>
    </Notice>
  );
}
