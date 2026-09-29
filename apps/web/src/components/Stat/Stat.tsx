import type { ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Stat.module.css";

export type StatTone = "good" | "warn" | "bad";

const TONE_GLYPH: Record<StatTone, string> = { good: "●", warn: "▲", bad: "✕" };
const TONE_WORD: Record<StatTone, string> = { good: "fine", warn: "needs a look", bad: "problem" };

export interface StatProps {
  value: number | string;
  label: string;
  hint?: string;
  tone?: StatTone;
  /** The one number a board is about. Twice the width, poster lettering. */
  feature?: boolean;
}

/**
 * A ticket stub with a number on it: notched at the perforation, value above,
 * label below. A tone adds colour AND a glyph, so it survives colour
 * blindness and greyscale screens.
 */
export function Stat({ value, label, hint, tone, feature = false }: StatProps) {
  return (
    <div className={cx(styles.stub, feature && styles.feature, tone && styles[tone])}>
      <p className={cx(styles.value, "tabular-nums")}>{value}</p>
      <div className={styles.foot}>
        <p className={styles.label}>
          {tone && (
            <span className={styles.toneGlyph} aria-hidden="true">
              {TONE_GLYPH[tone]}
            </span>
          )}
          {label}
          {tone && <span className="sr-only"> ({TONE_WORD[tone]})</span>}
        </p>
        {hint && <p className={styles.hint}>{hint}</p>}
      </div>
    </div>
  );
}

export function StatGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(styles.grid, className)}>{children}</div>;
}
