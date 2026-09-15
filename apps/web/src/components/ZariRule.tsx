import styles from "./ZariRule.module.css";

/**
 * The double-rule section divider — a 2px zari line, a 1px paper gap, a 2px
 * zari line. This replaces every `<hr>` and every "section" wrapper in the
 * app. Never a box-shadow, never a gradient.
 */
export function ZariRule() {
  return <div className={styles.rule} role="separator" />;
}
