import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { getPaymentStatus } from "../lib/mock";
import type { PaymentStatusResponse } from "../lib/types";

const POLL_MS = 5_000;

/**
 * Status (`/status/:intentId`) — one honest sentence about where things
 * stand. No fake progress bar for something a human is doing.
 */
export function Status() {
  const { intentId } = useParams<{ intentId: string }>();
  const navigate = useNavigate();
  const [status, setStatus] = useState<PaymentStatusResponse | null>(null);
  const statusRef = useRef<PaymentStatusResponse | null>(null);

  useEffect(() => {
    if (!intentId) return;
    let cancelled = false;

    async function poll() {
      const current = statusRef.current;
      if (current && (current.state === "APPROVED" || current.state === "CLOSED")) return;
      try {
        const result = await getPaymentStatus(intentId!);
        if (!cancelled) {
          statusRef.current = result;
          setStatus(result);
        }
      } catch {
        // A transient network error just leaves the last known state on
        // screen; the next poll will retry.
      }
    }

    void poll();
    const interval = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [intentId]);

  if (!intentId) {
    navigate("/", { replace: true });
    return null;
  }

  if (!status) {
    return (
      <Card>
        <h1>Payment status</h1>
        <p>Checking&hellip;</p>
      </Card>
    );
  }

  const lateNoSeat = status.flags?.includes("LATE_NO_SEAT");

  return (
    <Card>
      <h1>Payment status</h1>

      {lateNoSeat ? (
        <p>Received. The event filled up before this arrived, so a human is reviewing it personally.</p>
      ) : status.state === "PENDING_APPROVAL" ? (
        <p>Submitted. We usually approve within a few hours — you&rsquo;ll get an email.</p>
      ) : status.state === "APPROVED" ? (
        <>
          <p>Approved. Your pass is ready.</p>
          <Button variant="primary" onClick={() => navigate("/pass")}>
            View your pass
          </Button>
        </>
      ) : status.state === "REJECTED" ? (
        <>
          <p>{status.reason ?? "Your payment couldn't be verified."}</p>
          <p>You can submit new proof once.</p>
          <Button variant="primary" onClick={() => navigate(`/buy/proof/${intentId}`)}>
            Submit new proof
          </Button>
        </>
      ) : status.state === "CLOSED" ? (
        <>
          <p>{status.reason ?? "This payment could not be verified."}</p>
          <p>
            Ask a human — reply to any email from us, or reach the help desk at the event, and we&rsquo;ll
            sort it out.
          </p>
        </>
      ) : (
        <p>We don&rsquo;t have an update on this payment yet.</p>
      )}

      <p>
        <Link to="/">Back to the invitation</Link>
      </p>
    </Card>
  );
}
