import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Field.module.css";

export interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  id: string;
  label: ReactNode;
  /** Keep the label for assistive tech but do not draw it. */
  hideLabel?: boolean;
  hint?: ReactNode;
  error?: ReactNode;
  inputSize?: "md" | "lg";
}

/** A labelled text input with its hint and error wired to it. */
export const Field = forwardRef<HTMLInputElement, FieldProps>(function Field(
  { id, label, hideLabel = false, hint, error, inputSize = "md", className, ...input },
  ref,
) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className={cx(styles.field, className)}>
      <label className={cx(styles.label, hideLabel && "sr-only")} htmlFor={id}>
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        className={cx(styles.input, styles[inputSize], error ? styles.invalid : undefined)}
        aria-invalid={error ? true : undefined}
        aria-describedby={cx(hintId, errorId) || undefined}
        {...input}
      />
      {hint && (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className={styles.error} role="alert">
          <span aria-hidden="true">✕</span> {error}
        </p>
      )}
    </div>
  );
});

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  children: ReactNode;
}

/** A real checkbox, visually replaced by an inked box with a hand-drawn tick. */
export function Checkbox({ children, className, ...input }: CheckboxProps) {
  return (
    <label className={cx(styles.check, className)}>
      <input type="checkbox" className={styles.native} {...input} />
      <span className={styles.box} aria-hidden="true">
        <svg viewBox="0 0 20 20" focusable="false">
          <path d="M4 10.5 L8.5 15 L16.5 4.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className={styles.checkText}>{children}</span>
    </label>
  );
}
