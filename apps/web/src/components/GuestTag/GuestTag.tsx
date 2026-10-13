import type { GuestTag as GuestTagValue } from "../../lib/types";
import { cx } from "../cx";
import styles from "./GuestTag.module.css";

/**
 * The guest tag (client, 14 Oct 2026): every guest is a baarati. Fixed at
 * minting -- this only ever displays it. A word and a glyph, never colour
 * alone; ♫ for the baarat, the band-led procession.
 */
const LOOK: Record<GuestTagValue, { label: string; glyph: string }> = {
  BAARATI: { label: "Baarati", glyph: "♫" },
};

export interface GuestTagProps {
  tag: GuestTagValue;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function GuestTag({ tag, size = "md", className }: GuestTagProps) {
  const look = LOOK[tag];
  return (
    <span className={cx(styles.tag, styles[tag], styles[size], className)}>
      <span className={styles.glyph} aria-hidden="true">
        {look.glyph}
      </span>
      <span>{look.label}</span>
    </span>
  );
}
