import type { ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Stamp.module.css";

export type StampTone = "good" | "warn" | "bad" | "neutral" | "paper";

export interface StampProps {
  tone?: StampTone;
  /** A shape that differs per state, so colour is never the only signal. */
  glyph?: string;
  size?: "sm" | "md" | "lg" | "xl";
  /** Land with a thump, once. For states that just changed. */
  animate?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * A rubber stamp: double inked ring, slightly askew, the ink a little patchy.
 * Used for every "what state is this in" answer — the pass, a scan result, a
 * mail row — so the same idea always looks the same.
 */
export function Stamp({ tone = "neutral", glyph, size = "md", animate = false, className, children }: StampProps) {
  return (
    <span className={cx(styles.stamp, styles[tone], styles[size], animate && styles.animate, className)}>
      {glyph && (
        <span className={styles.glyph} aria-hidden="true">
          {glyph}
        </span>
      )}
      <span>{children}</span>
    </span>
  );
}
