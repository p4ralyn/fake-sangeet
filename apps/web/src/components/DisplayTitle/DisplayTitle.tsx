import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./DisplayTitle.module.css";

export interface DisplayTitleProps extends HTMLAttributes<HTMLHeadingElement> {
  as?: "h1" | "h2" | "h3" | "p" | "span";
  size?: "hero" | "page" | "section";
  /**
   * marigold — the poster lettering, for scarlet, rani and ink grounds.
   * ink — for the bare ochre ground, where scarlet fails contrast.
   * scarlet — for paper.
   */
  tone?: "marigold" | "ink" | "scarlet";
  children: ReactNode;
}

/**
 * Shrikhand, set like the poster's title: a fill, an inked outline, and a
 * stacked extrusion built from hard (never blurred) text-shadows.
 */
export function DisplayTitle({
  as: Tag = "h1",
  size = "page",
  tone = "marigold",
  className,
  children,
  ...rest
}: DisplayTitleProps) {
  return (
    <Tag className={cx(styles.title, styles[size], styles[tone], className)} {...rest}>
      {children}
    </Tag>
  );
}
