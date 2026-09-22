import { useEffect } from "react";
import styles from "./ScanResultScreen.module.css";

/**
 * The result a volunteer sees after a scan resolves (LLD-v1 §11.5).
 *
 * Three fields, not two. Admitted is green, refused is scarlet, and a
 * duplicate — the suppressed re-serve of a scan that already happened — gets
 * its own maroon field, because it is neither. Operators double-tap
 * constantly; a repeat tap that flashed red would teach them to distrust the
 * red that matters.
 *
 * Colour is never the only signal. Every field carries a distinct glyph, a
 * distinct word, and (from `resultFeedback`) a distinct tone and haptic — so
 * the screen still reads for a colour-blind operator, and still reads for an
 * operator who is looking at the queue instead of the phone.
 *
 * Only the admitting results clear themselves. A refusal stays until it is
 * dismissed: the volunteer has to read a reason and act on it, and a screen
 * that vanished after two seconds would send people through by default.
 */

export type ResultTone = "admitted" | "refused" | "duplicate";

/** §11.5: "2 s auto-dismiss, hands-free". */
const AUTO_DISMISS_MS = 2000;

const GLYPHS: Record<ResultTone, string> = {
  admitted: "✓",
  refused: "✕",
  duplicate: "↻",
};

export interface ScanResultScreenProps {
  tone: ResultTone;
  /** The headline, in the operator's language: "Checked in", "Do not admit". */
  verdict: string;
  name: string | null;
  /** What to do next, when there is anything to do. */
  detail: string | null;
  onDismiss: () => void;
}

export function ScanResultScreen({ tone, verdict, name, detail, onDismiss }: ScanResultScreenProps) {
  const selfClearing = tone !== "refused";

  useEffect(() => {
    if (!selfClearing) {
      return;
    }
    const timer = window.setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [selfClearing, onDismiss]);

  return (
    <section
      className={`${styles.screen} ${styles[tone]}`}
      role="alert"
      aria-live="assertive"
      // The whole field is the dismiss target: at a gate, a thumb lands
      // somewhere on the screen, not on a 44px rectangle.
      onClick={onDismiss}
    >
      <p className={styles.glyph} aria-hidden="true">
        {GLYPHS[tone]}
      </p>
      <h1 className={styles.verdict}>{verdict}</h1>
      {name && <p className={styles.name}>{name}</p>}
      {detail && <p className={styles.detail}>{detail}</p>}
      <p className={styles.hint}>{selfClearing ? "Clearing…" : "Tap to scan the next person"}</p>
    </section>
  );
}
