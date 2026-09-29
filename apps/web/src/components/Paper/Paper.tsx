import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Paper.module.css";

export interface PaperProps extends HTMLAttributes<HTMLElement> {
  as?: "div" | "section" | "article" | "aside";
  /** Hard ink offset. Only for cut-outs that sit ON the poster — the pass, a
   * decision card, a hero tag. Reading panels stay flat. */
  lift?: "none" | "sm" | "md" | "lg";
  /** A couple of degrees, as if pasted on by hand. */
  tilt?: "left" | "right";
  /** Two strips of masking tape across the top corners. */
  tape?: boolean;
  pad?: "sm" | "md" | "lg";
  children: ReactNode;
}

/**
 * The reading surface: paper, inked edge, scissored corners. Every block of
 * body text in the app sits on one of these, never on the bare ochre ground.
 */
export function Paper({
  as: Tag = "div",
  lift = "none",
  tilt,
  tape = false,
  pad = "md",
  className,
  children,
  ...rest
}: PaperProps) {
  return (
    <Tag
      className={cx(
        styles.paper,
        styles[`pad-${pad}`],
        lift !== "none" && styles[`lift-${lift}`],
        tilt && styles[`tilt-${tilt}`],
        tape && styles.taped,
        className,
      )}
      {...rest}
    >
      {tape && (
        <>
          <span className={cx(styles.tape, styles.tapeLeft)} aria-hidden="true" />
          <span className={cx(styles.tape, styles.tapeRight)} aria-hidden="true" />
        </>
      )}
      {children}
    </Tag>
  );
}
