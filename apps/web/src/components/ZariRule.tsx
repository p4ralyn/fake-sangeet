import styles from "./ZariRule.module.css";

export interface FilmStripBandProps {
  /** Horizontal divider (default) or a vertical rail along a card edge. */
  orientation?: "horizontal" | "vertical";
  /** Smaller pitch and thickness, for the pass ticket's own perforation. */
  thin?: boolean;
  className?: string;
}

/**
 * The film-strip band — a scarlet reel with evenly spaced cream sprocket
 * holes and inked maroon edges. Replaces the old double hairline as the
 * section divider (docs/design/frontend-design-spec.md, "Ornament"), and
 * doubles as the pass ticket's perforation and edge rails.
 *
 * Built as inline SVG using a tiled `<pattern>` rather than a CSS gradient
 * (gradients are banned app-wide) or a fixed viewBox (which would squash the
 * holes into ellipses at some container widths). The `<svg>` here carries no
 * `viewBox`, so its internal coordinate system is 1:1 with real pixels at
 * whatever size it renders — the pattern always tiles at a true pixel pitch,
 * so the holes stay circular at every width.
 *
 * Purely decorative: aria-hidden, and it never sits under a text run.
 */
export function ZariRule({ orientation = "horizontal", thin = false, className }: FilmStripBandProps) {
  const isVertical = orientation === "vertical";
  const thickness = thin ? 12 : 18;
  const pitch = thin ? 16 : 26;
  const holeRadius = thin ? 2.25 : 4;

  const tileWidth = isVertical ? thickness : pitch;
  const tileHeight = isVertical ? pitch : thickness;
  const patternId = `filmstrip-${orientation}-${thin ? "thin" : "wide"}`;

  const wrapperClass = [styles.rule, isVertical ? styles.vertical : styles.horizontal, thin ? styles.thin : "", className]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={wrapperClass} aria-hidden="true">
      <svg className={styles.svg} width="100%" height="100%" focusable="false">
        <defs>
          <pattern id={patternId} patternUnits="userSpaceOnUse" width={tileWidth} height={tileHeight}>
            <rect width={tileWidth} height={tileHeight} fill="#C8102E" />
            <circle cx={tileWidth / 2} cy={tileHeight / 2} r={holeRadius} fill="#FFF3DC" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${patternId})`} />
        {isVertical ? (
          <>
            <rect x="0" y="0" width="2" height="100%" fill="#5E0C18" />
            <rect x={thickness - 2} y="0" width="2" height="100%" fill="#5E0C18" />
          </>
        ) : (
          <>
            <rect x="0" y="0" width="100%" height="2" fill="#5E0C18" />
            <rect x="0" y={thickness - 2} width="100%" height="2" fill="#5E0C18" />
          </>
        )}
      </svg>
    </div>
  );
}
