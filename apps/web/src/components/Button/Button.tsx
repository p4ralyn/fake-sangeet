import type { ButtonHTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { cx } from "../cx";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "marigold" | "good" | "danger" | "quiet";
export type ButtonSize = "md" | "lg";

interface Look {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Full width of its container. */
  block?: boolean;
}

/** The class list for a button-looking thing, for the rare element that is
 * neither a <button> nor a router <Link> (a plain <a href> out of the SPA). */
export function buttonClass({ variant = "primary", size = "md", block = false }: Look = {}, className?: string) {
  return cx(styles.button, styles[variant], styles[size], block && styles.block, className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, Look {}

/**
 * Sentence-case labels that say what happens, no trailing arrow. Primary is
 * a cut-out with a hard ink shadow that it presses into when tapped. 48px
 * minimum height; 56px at `lg`.
 */
export function Button({ variant, size, block, className, type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={buttonClass({ variant, size, block }, className)} {...rest} />;
}

export interface ButtonLinkProps extends LinkProps, Look {}

/** A router link that looks like a button, for actions that are navigation. */
export function ButtonLink({ variant, size, block, className, ...rest }: ButtonLinkProps) {
  return <Link className={buttonClass({ variant, size, block }, className)} {...rest} />;
}
