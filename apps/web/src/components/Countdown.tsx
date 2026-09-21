import { useEffect, useRef, useState } from "react";
import styles from "./Countdown.module.css";

const URGENT_THRESHOLD_MS = 5 * 60 * 1000;

export interface CountdownProps {
  /** ISO 8601 expiry timestamp, e.g. a hold's `hold_expires_at`. */
  expiresAt: string;
  /** Fires once, the instant the countdown reaches zero. */
  onExpire?: () => void;
  className?: string;
}

function formatRemaining(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Renders mm:ss in tabular numerals, turns --magenta at 5 minutes remaining,
 * and fires onExpire the moment it reaches zero. Self-expires client-side
 * rather than sitting there looking valid — a stale hold must never read as
 * a live one.
 */
export function Countdown({ expiresAt, onExpire, className }: CountdownProps) {
  const expiryMs = useRef(new Date(expiresAt).getTime());
  const [remaining, setRemaining] = useState(() => expiryMs.current - Date.now());
  const firedRef = useRef(false);

  useEffect(() => {
    expiryMs.current = new Date(expiresAt).getTime();
    firedRef.current = false;
    setRemaining(expiryMs.current - Date.now());

    const tick = () => {
      const left = expiryMs.current - Date.now();
      setRemaining(left);
      if (left <= 0 && !firedRef.current) {
        firedRef.current = true;
        onExpire?.();
      }
    };

    const interval = window.setInterval(tick, 1000);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiresAt]);

  const expired = remaining <= 0;
  const urgent = !expired && remaining <= URGENT_THRESHOLD_MS;
  const cls = [
    styles.countdown,
    "tabular-nums",
    expired ? styles.expired : urgent ? styles.urgent : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={cls} role="timer" aria-live="polite">
      {expired ? "Expired" : formatRemaining(remaining)}
    </span>
  );
}
