import type { ReactNode } from "react";
import { Paper } from "../Paper/Paper";
import { Sparkle } from "../Sparkle";
import styles from "./EmptyState.module.css";

export interface EmptyStateProps {
  title: ReactNode;
  /** The heading level; a screen whose whole content this is wants h1. */
  headingAs?: "h1" | "h2";
  action?: ReactNode;
  children?: ReactNode;
}

/** A screen with nothing to show: say so, say why, offer the next step. */
export function EmptyState({ title, headingAs: Heading = "h1", action, children }: EmptyStateProps) {
  return (
    <Paper as="section" pad="lg" tape className={styles.empty}>
      <Sparkle className={styles.sparkle} />
      <Heading className={styles.title}>{title}</Heading>
      {children && <div className={styles.body}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </Paper>
  );
}
