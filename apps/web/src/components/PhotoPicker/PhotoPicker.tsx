import type { ReactNode } from "react";
import styles from "./PhotoPicker.module.css";

export interface PhotoPickerProps {
  /** Object URL of the chosen photo, or null before one is chosen. */
  preview: string | null;
  previewAlt: string;
  /** The control that opens the camera — owned by the caller, with its input. */
  action: ReactNode;
}

/**
 * A viewfinder for the ID photo: corner brackets and a card outline until a
 * photo is chosen, then the photo itself in the same frame. Presentational —
 * the file input, the object URL and its cleanup stay with the screen.
 */
export function PhotoPicker({ preview, previewAlt, action }: PhotoPickerProps) {
  return (
    <div className={styles.picker}>
      <div className={styles.frame}>
        <span className={`${styles.corner} ${styles.tl}`} aria-hidden="true" />
        <span className={`${styles.corner} ${styles.tr}`} aria-hidden="true" />
        <span className={`${styles.corner} ${styles.bl}`} aria-hidden="true" />
        <span className={`${styles.corner} ${styles.br}`} aria-hidden="true" />
        {preview ? (
          <img className={styles.preview} src={preview} alt={previewAlt} />
        ) : (
          <svg className={styles.card} viewBox="0 0 160 100" aria-hidden="true" focusable="false">
            <rect x="3" y="3" width="154" height="94" rx="8" fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray="8 6" />
            <rect x="16" y="22" width="36" height="46" rx="4" fill="currentColor" opacity="0.35" />
            <rect x="64" y="26" width="76" height="8" rx="4" fill="currentColor" opacity="0.35" />
            <rect x="64" y="42" width="56" height="8" rx="4" fill="currentColor" opacity="0.35" />
            <rect x="64" y="58" width="66" height="8" rx="4" fill="currentColor" opacity="0.35" />
          </svg>
        )}
      </div>
      {action}
    </div>
  );
}
