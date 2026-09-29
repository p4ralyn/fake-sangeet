import type { ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Notice.module.css";

export type NoticeTone = "info" | "wait" | "good" | "warn" | "error";

const GLYPHS: Record<NoticeTone, string> = {
  info: "✦",
  wait: "◔",
  good: "✓",
  warn: "!",
  error: "✕",
};

export interface NoticeProps {
  tone?: NoticeTone;
  /** Announce it when it appears (errors after an action). */
  live?: boolean;
  className?: string;
  children: ReactNode;
}

/**
 * An inline message about the state of things: a tone tab with its own
 * glyph, then the words. Colour, glyph and wording all carry the tone.
 */
export function Notice({ tone = "info", live = false, className, children }: NoticeProps) {
  return (
    <div className={cx(styles.notice, styles[tone], className)} role={live ? "alert" : undefined}>
      <span className={styles.tab} aria-hidden="true">
        {GLYPHS[tone]}
      </span>
      <div className={styles.body}>{children}</div>
    </div>
  );
}
