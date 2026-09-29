import type { ReactNode } from "react";
import { cx } from "../cx";
import styles from "./FilmStrip.module.css";

export interface FilmStripProps {
  /** Horizontal band (default) or a vertical rail. */
  orientation?: "horizontal" | "vertical";
  size?: "thin" | "regular";
  /**
   * With children the strip becomes a real length of film: a row of
   * sprockets, the frames, another row of sprockets. Without, it is a single
   * sprocket band — the divider.
   */
  children?: ReactNode;
  className?: string;
  framesClassName?: string;
}

/**
 * The scarlet reel from the poster, cut to length. The sprocket holes are an
 * SVG tile, so they stay crisp rectangles at every width.
 */
export function FilmStrip({
  orientation = "horizontal",
  size = "regular",
  children,
  className,
  framesClassName,
}: FilmStripProps) {
  const classes = cx(styles.strip, styles[orientation], styles[size], className);

  if (children === undefined) {
    return <div className={cx(classes, "ornament")} aria-hidden="true"><span className={styles.holes} /></div>;
  }

  return (
    <div className={cx(classes, styles.withFrames)}>
      <span className={styles.holes} aria-hidden="true" />
      <div className={cx(styles.frames, framesClassName)}>{children}</div>
      <span className={styles.holes} aria-hidden="true" />
    </div>
  );
}
