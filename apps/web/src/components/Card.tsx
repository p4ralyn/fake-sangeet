import type { HTMLAttributes, ReactNode } from "react";
import styles from "./Card.module.css";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/** Cream surface. 2px maroon border, 2px radius, no shadow — ever. */
export function Card({ children, className, ...rest }: CardProps) {
  const cls = className ? `${styles.card} ${className}` : styles.card;
  return (
    <div className={cls} {...rest}>
      {children}
    </div>
  );
}
