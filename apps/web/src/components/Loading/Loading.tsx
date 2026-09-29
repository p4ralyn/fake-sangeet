import type { ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Loading.module.css";

/** A film reel, turning. */
export function ReelSpinner({ className }: { className?: string }) {
  return (
    <svg className={cx(styles.reel, "ornament", className)} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <circle cx="24" cy="24" r="21" fill="#c8102e" stroke="#4a0a14" strokeWidth="2.5" />
      {[0, 72, 144, 216, 288].map((angle) => (
        <circle key={angle} cx="24" cy="11.5" r="5.2" fill="#fff3dc" stroke="#4a0a14" strokeWidth="1.5" transform={`rotate(${angle} 24 24)`} />
      ))}
      <circle cx="24" cy="24" r="3.5" fill="#ffc61a" stroke="#4a0a14" strokeWidth="1.5" />
    </svg>
  );
}

export interface LoadingProps {
  children?: ReactNode;
  /** Inline sits in a panel; page centres itself in the column. */
  variant?: "page" | "inline";
}

/** A reel and one line saying what we are waiting for. */
export function Loading({ children = "Loading…", variant = "page" }: LoadingProps) {
  return (
    <div className={cx(styles.loading, styles[variant])} role="status" aria-live="polite">
      <ReelSpinner />
      <p className={styles.label}>{children}</p>
    </div>
  );
}
