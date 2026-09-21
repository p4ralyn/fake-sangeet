import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Countdown } from "../components/Countdown";
import { Stepper } from "../components/Stepper";
import { ZariRule } from "../components/ZariRule";
import { ApiError } from "../lib/api";
import { formatClockTime, formatRupees } from "../lib/format";
import { getPaymentPage } from "../lib/mock";
import type { PaymentPageResponse } from "../lib/types";
import styles from "./Payment.module.css";

const STEPS = ["Choose", "Details", "Payment"];

function CopyableRow({
  label,
  value,
  variant = "default",
}: {
  label: string;
  value: string;
  variant?: "default" | "highlight";
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard permission can be denied — the value is still selectable
      // text, so this is a convenience, not the only way to get it.
    }
  }

  const isHighlight = variant === "highlight";

  return (
    <div className={isHighlight ? `${styles.copyRow} ${styles.copyRowHighlight}` : styles.copyRow}>
      <span className={isHighlight ? `${styles.copyLabel} ${styles.copyLabelHighlight}` : styles.copyLabel}>
        {label}
      </span>
      <span className={`${isHighlight ? styles.copyValueHighlight : styles.copyValue} tabular-nums`}>{value}</span>
      <Button
        type="button"
        variant="quiet"
        className={isHighlight ? styles.copyButtonHighlight : undefined}
        onClick={handleCopy}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/**
 * Payment (`/buy/pay/:intentId`) — step 3 of 3. Reachable only with a live
 * hold; a 403 on arrival routes silently back to step 1 (addendum §A2). The
 * countdown self-expires client-side into a two-path recovery state.
 */
export function Payment() {
  const { intentId } = useParams<{ intentId: string }>();
  const navigate = useNavigate();
  const [page, setPage] = useState<PaymentPageResponse | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (!intentId) {
      navigate("/", { replace: true });
      return;
    }
    let cancelled = false;
    getPaymentPage(intentId)
      .then((result) => {
        if (!cancelled) setPage(result);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) {
          // Arrived somewhere we were never allowed — no error, just back
          // to step 1 (docs/design/participant-screens.md §4).
          navigate("/", { replace: true });
        } else {
          setNotFound(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [intentId, navigate]);

  if (!intentId) return null;

  if (notFound) {
    return (
      <Card>
        <p>We couldn&rsquo;t reach the server. Please try again.</p>
      </Card>
    );
  }

  if (!page) {
    return (
      <Card>
        <Stepper steps={STEPS} currentIndex={2} />
        <p>Loading your reservation&hellip;</p>
      </Card>
    );
  }

  return (
    <Card>
      <Stepper steps={STEPS} currentIndex={2} />
      <h1>Complete payment</h1>

      {expired ? (
        <div className={styles.recovery}>
          <p>
            Your reservation expired. If you haven&rsquo;t paid, you can start again — seats may still be
            available. If you have already paid, submit your proof here and we&rsquo;ll review it.
          </p>
          <div className={styles.recoveryActions}>
            <Button variant="quiet" onClick={() => navigate("/buy")}>
              Start again
            </Button>
            <Button variant="primary" onClick={() => navigate(`/buy/proof/${intentId}`)}>
              Submit your proof
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className={styles.countdownBlock}>
            <Countdown expiresAt={page.hold_expires_at} onExpire={() => setExpired(true)} />
            <p className={styles.countdownNote}>
              Your seats are held until <span className="tabular-nums">{formatClockTime(page.hold_expires_at)}</span>.
              Don&rsquo;t close this page.
            </p>
          </div>

          <ZariRule />

          <div className={styles.amountBlock}>
            <span className={styles.amountLabel}>Amount</span>
            <span className={`${styles.amount} tabular-nums`}>{formatRupees(page.amount)}</span>
          </div>

          <div className={styles.qrBlock}>
            <QRCodeSVG value={page.upi_deep_link} size={200} bgColor="#FFF3DC" fgColor="#5E0C18" level="M" />
          </div>

          <CopyableRow label="UPI ID" value={page.upi_vpa} variant="highlight" />

          <a className={styles.deepLinkButton} href={page.upi_deep_link}>
            Open your UPI app
          </a>

          <ZariRule />

          <CopyableRow label="Reference code" value={page.reference_code} />
          <p className={styles.hint}>Add this to the payment note — it&rsquo;s how we match your payment.</p>

          <Button variant="primary" onClick={() => navigate(`/buy/proof/${intentId}`)}>
            I&rsquo;ve paid
          </Button>
        </>
      )}
    </Card>
  );
}
