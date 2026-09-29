import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Plaque.module.css";

export interface PlaqueProps extends HTMLAttributes<HTMLDivElement> {
  tone?: "rani" | "scarlet" | "ink" | "paper";
  size?: "sm" | "md" | "lg";
  /** Hard ink offset under the whole shape. */
  lifted?: boolean;
  children: ReactNode;
}

/**
 * The spons deck's title frame: a slab with scooped corners, an inked
 * outline that follows the scoops, and a dotted marigold rule inside. Titles
 * set on it read as a sign-painted board rather than a coloured box.
 *
 * The scoops are a mask, so the outline is a second, slightly larger masked
 * layer behind the face; the lift is a `drop-shadow` filter with no blur so it
 * follows the cut shape rather than the bounding box.
 */
export function Plaque({ tone = "rani", size = "md", lifted = true, className, children, ...rest }: PlaqueProps) {
  return (
    <div className={cx(styles.plaque, styles[size], lifted && styles.lifted, className)} {...rest}>
      <div className={styles.frame}>
        <div className={cx(styles.face, styles[tone])}>{children}</div>
      </div>
    </div>
  );
}
