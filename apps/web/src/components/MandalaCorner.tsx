import styles from "./MandalaCorner.module.css";

const CORNER_CLASS = {
  "top-left": styles.topLeft,
  "top-right": styles.topRight,
  "bottom-left": styles.bottomLeft,
  "bottom-right": styles.bottomRight,
} as const;

export interface MandalaCornerProps {
  corner: keyof typeof CORNER_CLASS;
}

const PETAL_COUNT = 10;

/**
 * A mandala rosette, fixed at a page corner, drawn once at a viewport
 * corner so only the quarter nearest that corner is ever on screen (see
 * docs/design/frontend-design-spec.md, "Ornament" — mandala quarter-
 * rosettes, as in deck-1.jpg). Inline SVG, radially symmetric so no
 * per-corner artwork is needed; each instance is just re-anchored.
 *
 * Sits behind all content (z-index 0, page content stacks above it),
 * ignores pointer events, and is hidden from assistive tech — it never
 * carries information, only mood.
 */
export function MandalaCorner({ corner }: MandalaCornerProps) {
  const petals = Array.from({ length: PETAL_COUNT }, (_, i) => (360 / PETAL_COUNT) * i);

  return (
    <svg
      className={`${styles.corner} ${CORNER_CLASS[corner]} ornament`}
      viewBox="0 0 220 220"
      aria-hidden="true"
      focusable="false"
    >
      <g transform="translate(110 110)">
        <circle r="98" fill="none" stroke="#D6006E" strokeWidth="1.5" opacity="0.55" />
        <circle r="78" fill="none" stroke="#D6006E" strokeWidth="1.5" opacity="0.55" />
        <circle r="24" fill="none" stroke="#D6006E" strokeWidth="1.5" opacity="0.7" />

        {petals.map((angle) => (
          <g key={angle} transform={`rotate(${angle})`}>
            <path d="M0,-30 L14,-64 Q0,-84 -14,-64 Z" fill="#FFC61A" stroke="#D6006E" strokeWidth="1.5" />
            <circle cx="0" cy="-92" r="5" fill="#C8102E" />
          </g>
        ))}

        <circle r="14" fill="#C8102E" />
        <circle r="6" fill="#FFF3DC" />
      </g>
    </svg>
  );
}
