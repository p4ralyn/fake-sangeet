export interface SparkleProps {
  className?: string;
}

/**
 * A small firework burst — a four-point star with two flecks — used
 * sparingly beside headings, as the pitch deck does around "Welcome to".
 * Inline SVG, aria-hidden, beside text only, never under it.
 */
export function Sparkle({ className }: SparkleProps) {
  return (
    <svg
      className={["ornament", className].filter(Boolean).join(" ")}
      width="28"
      height="28"
      viewBox="0 0 28 28"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M14 1 L16.4 11.6 L27 14 L16.4 16.4 L14 27 L11.6 16.4 L1 14 L11.6 11.6 Z"
        fill="#ffc61a"
        stroke="#4a0a14"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="5" r="2.2" fill="#e0147c" stroke="#4a0a14" strokeWidth="0.8" />
      <circle cx="4" cy="23" r="1.6" fill="#c8102e" />
    </svg>
  );
}
