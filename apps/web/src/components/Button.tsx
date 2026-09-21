import type { ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "quiet";
}

/**
 * Primary (scarlet ground) and quiet variants. Sentence case labels, no `→`.
 * 44px minimum touch target on both axes.
 */
export function Button({ variant = "primary", className, ...rest }: ButtonProps) {
  const variantClass = variant === "primary" ? styles.primary : styles.quiet;
  const cls = className ? `${styles.button} ${variantClass} ${className}` : `${styles.button} ${variantClass}`;
  return <button className={cls} {...rest} />;
}
