export interface SparkleProps {
  className?: string;
}

/**
 * A small firework/sparkle burst — a four-point star with two short
 * radiating ticks — used sparingly near headings (deck-2.jpg has several of
 * these around "Welcome to"). Inline SVG, aria-hidden, behind/beside text
 * only, never under it.
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
      <path d="M14 1 L16.4 11.6 L27 14 L16.4 16.4 L14 27 L11.6 16.4 L1 14 L11.6 11.6 Z" fill="#FFC61A" stroke="#D6006E" strokeWidth="1" />
      <circle cx="24" cy="5" r="2" fill="#D6006E" />
      <circle cx="4" cy="23" r="1.5" fill="#C8102E" />
    </svg>
  );
}
