import type { GuestTag as GuestTagValue } from "../../lib/types";
import { cx } from "../cx";
import styles from "./GuestTag.module.css";

/**
 * Which side of the wedding a guest is on: baarati or gharwale (client,
 * 14 Oct 2026). Fixed at minting -- this only ever displays it.
 *
 * Colour is never the only signal: each side has its own word AND its own
 * glyph, so the badge reads for a colour-blind volunteer and in a dim
 * doorway. ♫ for the baarat, the band-led procession; ⌂ for the gharwale, the
 * home side.
 */
const LOOK: Record<GuestTagValue, { label: string; glyph: string }> = {
  BAARATI: { label: "Baarati", glyph: "♫" },
  GHARWALE: { label: "Gharwale", glyph: "⌂" },
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
