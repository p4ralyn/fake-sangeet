import { cx } from "../cx";
import styles from "./Garland.module.css";

export interface GarlandProps {
  side: "left" | "right";
  className?: string;
}

// Beads per string, varied so the row reads hand-hung rather than printed.
const STRINGS = [7, 11, 5, 9, 6];
const BEAD = 13;

/**
 * Hanging marigold strings, as they hang from the top of the spons deck. A
 * cluster of strings at one side of the page; the centre stays clear.
 */
export function Garland({ side, className }: GarlandProps) {
  const ordered = side === "left" ? STRINGS : [...STRINGS].reverse();
  const longest = Math.max(...STRINGS);
  const height = longest * BEAD + 30;
  const width = STRINGS.length * 26;

  return (
    <svg
      className={cx(styles.garland, styles[side], "ornament", className)}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      aria-hidden="true"
      focusable="false"
    >
      {ordered.map((beads, s) => {
        const x = 13 + s * 26;
        const end = beads * BEAD + 6;
        return (
          <g key={s} className={styles.string} style={{ animationDelay: `${s * 70}ms` }}>
            <line x1={x} y1={0} x2={x} y2={end} stroke="#4a0a14" strokeWidth="1.5" />
            {Array.from({ length: beads }, (_, b) => (
              <circle
                key={b}
                cx={x}
                cy={8 + b * BEAD}
                r={b % 3 === 2 ? 5 : 6.5}
                fill={b % 3 === 2 ? "#e0147c" : b % 2 ? "#f08a00" : "#ffc61a"}
                stroke="#4a0a14"
                strokeWidth="1.2"
              />
            ))}
            {/* a small brass bell at the end */}
            <path
              d={`M${x - 6} ${end + 12} Q${x - 6} ${end + 2} ${x} ${end + 1} Q${x + 6} ${end + 2} ${x + 6} ${end + 12} Z`}
              fill="#ffc61a"
              stroke="#4a0a14"
              strokeWidth="1.2"
            />
            <circle cx={x} cy={end + 14} r="2" fill="#4a0a14" />
          </g>
        );
      })}
    </svg>
  );
}
