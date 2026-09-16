import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Field } from "../components/Field";
import { ApiError } from "../lib/api";
import { getHoldDetails } from "../lib/holdStore";
import { requestProofUpload, submitProof } from "../lib/mock";
import { useSession } from "../lib/session";
import styles from "./Proof.module.css";

const MAX_BYTES = 5 * 1024 * 1024;
const UTR_PATTERN = /^\d{12}$/;

/**
 * Submit your proof (`/buy/proof/:intentId`) — reachable both from a live
 * payment page ("I've paid") and from an expired hold's recovery state
 * (which produces a LATE_NO_SEAT-flagged submission routed to a human).
 */
export function Proof() {
  const { intentId } = useParams<{ intentId: string }>();
  const navigate = useNavigate();
  const { user } = useSession();

  const stored = intentId ? getHoldDetails(intentId) : null;

  const [utr, setUtr] = useState("");
  const [utrError, setUtrError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [payerName, setPayerName] = useState(stored?.payerName ?? user?.display_name ?? "");
  const [payerPhone, setPayerPhone] = useState(stored?.payerPhone ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [duplicateUtr, setDuplicateUtr] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  if (!intentId) {
    navigate("/", { replace: true });
    return null;
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFileError(null);
    setFile(null);
    if (preview) {
      URL.revokeObjectURL(preview);
      setPreview(null);
    }

    const selected = event.target.files?.[0];
    if (!selected) return;

    if (!selected.type.startsWith("image/")) {
      setFileError("That's not an image. Upload a screenshot (JPG, PNG or similar).");
      return;
    }
    if (selected.size > MAX_BYTES) {
      setFileError("That file is larger than 5 MB. Try a smaller screenshot.");
      return;
    }

    setFile(selected);
    setPreview(URL.createObjectURL(selected));
  }

  function validateUtr(value: string): boolean {
    if (!UTR_PATTERN.test(value)) {
      setUtrError("Enter the 12-digit reference number from your UPI app.");
      return false;
    }
    setUtrError(null);
    return true;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setDuplicateUtr(false);

    const utrValid = validateUtr(utr);
    if (!utrValid || !file) {
      if (!file) setFileError((current) => current ?? "Attach a screenshot of your payment.");
      return;
    }

    setSubmitting(true);
    try {
      const { upload_url, object_key } = await requestProofUpload(intentId!);
      try {
        await fetch(upload_url, { method: "PUT", body: file });
      } catch {
        // Best-effort direct upload — the mock endpoint has nowhere real to
        // land this yet; submitProof below is what actually matters.
      }
      await submitProof(intentId!, {
        utr,
        payer_name: payerName,
        payer_phone: payerPhone,
        object_key,
      });
      navigate(`/status/${intentId}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "UTR_ALREADY_USED") {
        setDuplicateUtr(true);
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <h1>Submit your proof</h1>
      <form onSubmit={handleSubmit} noValidate>
        <Field
          label="UTR / reference number"
          value={utr}
          onChange={(e) => setUtr(e.target.value)}
          onBlur={(e) => e.target.value && validateUtr(e.target.value)}
          inputMode="numeric"
          maxLength={12}
          hint="The 12-digit number from your UPI app's transaction history."
          error={utrError ?? undefined}
          required
        />

        <div className={styles.fileField}>
          <label className={styles.fileLabel} htmlFor="proof-screenshot">
            Payment screenshot
          </label>
          <input
            id="proof-screenshot"
            type="file"
            accept="image/*"
            onChange={handleFileChange}
            className={styles.fileInput}
          />
          {fileError && (
            <span className={styles.fileError} role="alert">
              {fileError}
            </span>
          )}
          {preview && (
            <img className={styles.preview} src={preview} alt="Preview of your payment screenshot" />
          )}
        </div>

        <Field
          label="Payer name"
          value={payerName}
          onChange={(e) => setPayerName(e.target.value)}
          required
        />
        <Field
          label="Payer phone"
          type="tel"
          value={payerPhone}
          onChange={(e) => setPayerPhone(e.target.value)}
          required
        />

        {duplicateUtr && (
          <p className={styles.error} role="alert">
            That reference number has already been submitted. Check you&rsquo;ve copied the right one from
            your UPI app. If you think this is a mistake, ask a human.
          </p>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Submitting…" : "Submit proof"}
        </Button>
      </form>
    </Card>
  );
}
