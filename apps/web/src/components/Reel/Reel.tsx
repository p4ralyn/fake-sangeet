import { useId } from "react";
import { cx } from "../cx";
import styles from "./Reel.module.css";

export interface ReelProps {
  className?: string;
  /** Draw the reel on across the page once, on mount. */
  animate?: boolean;
}

// One sweep: in from the lower left, a loop under the title, out top right.
const PATH =
  "M-80 610 C 90 520, 200 700, 330 560 S 380 250, 520 250 S 700 470, 820 330 S 930 60, 1100 90";

/**
 * The poster's film reel, snaking behind the landing title. Built from
 * stacked strokes of one path — an ink outline, the scarlet base, a dashed
 * paper stroke for the sprockets, a scarlet stroke that covers the middle of
 * those dashes so only the two edge rows remain, then dark frame cells.
 * No offset-path maths, and it scales with the viewBox.
 */
export function Reel({ className, animate = false }: ReelProps) {
  const maskId = `reel-draw-${useId().replace(/:/g, "")}`;

  return (
    <svg
      className={cx(styles.reel, animate && styles.animate, "ornament", className)}
      viewBox="0 0 1000 700"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <mask id={maskId} maskUnits="userSpaceOnUse" x="-200" y="-200" width="1400" height="1100">
          <path className={styles.drawPath} d={PATH} pathLength={1} fill="none" stroke="#fff" strokeWidth="140" />
        </mask>
      </defs>
      <g mask={`url(#${maskId})`} fill="none" strokeLinecap="butt">
        <path d={PATH} stroke="#4a0a14" strokeWidth="112" />
        <path d={PATH} stroke="#c8102e" strokeWidth="104" />
        <path d={PATH} stroke="#fff3dc" strokeWidth="84" strokeDasharray="11 13" />
        <path d={PATH} stroke="#c8102e" strokeWidth="62" />
        <path d={PATH} stroke="#8e0b20" strokeWidth="50" strokeDasharray="58 7" />
      </g>
    </svg>
  );
}
