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

const OUTER_PETALS = 12;
const INNER_PETALS = 8;
const DOTS = 36;

const ring = (count: number) => Array.from({ length: count }, (_, i) => (360 / count) * i);

/**
 * A mandala rosette, fixed at a page corner, drawn once at a viewport
 * corner so only the quarter nearest that corner is ever on screen — the
 * pitch deck's corner rosettes. Inline SVG, radially symmetric so no
 * per-corner artwork is needed; each instance is just re-anchored.
 *
 * Sits behind all content, ignores pointer events, and is hidden from
 * assistive tech — it never carries information, only mood.
 */
export function MandalaCorner({ corner }: MandalaCornerProps) {
  return (
    <svg
      className={`${styles.corner} ${CORNER_CLASS[corner]} ornament`}
      viewBox="0 0 240 240"
      aria-hidden="true"
      focusable="false"
    >
      <g transform="translate(120 120)" stroke="#4a0a14" strokeWidth="1.6">
        {ring(OUTER_PETALS).map((angle) => (
          <path
            key={`o${angle}`}
            transform={`rotate(${angle})`}
            d="M0,-62 C 20,-78 22,-100 0,-116 C -22,-100 -20,-78 0,-62 Z"
            fill="#f08a00"
          />
        ))}
        <circle r="66" fill="#ffc61a" />
        {ring(DOTS).map((angle) => (
          <circle key={`d${angle}`} transform={`rotate(${angle})`} cy="-58" r="2.6" fill="#4a0a14" stroke="none" />
        ))}
        <circle r="50" fill="#e0147c" />
        {ring(INNER_PETALS).map((angle) => (
          <path
            key={`i${angle}`}
            transform={`rotate(${angle + 22.5})`}
            d="M0,-14 C 12,-24 12,-38 0,-47 C -12,-38 -12,-24 0,-14 Z"
            fill="#fff3dc"
          />
        ))}
        <circle r="16" fill="#c8102e" />
        <circle r="6" fill="#ffc61a" />
      </g>
    </svg>
  );
}
