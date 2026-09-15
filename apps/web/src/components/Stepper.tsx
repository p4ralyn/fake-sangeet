import styles from "./Stepper.module.css";

export interface StepperProps {
  steps: string[];
  /** Zero-based index of the current step. */
  currentIndex: number;
}

/**
 * The purchase sequence indicator. The flow genuinely IS a sequence, so this
 * is justified — but it is set as invitation rules (small names, a zari tick
 * between them, the current step underlined), never as `01 / 02 / 03` chips.
 * Completed/current state never relies on colour alone: a "done" mark and an
 * underline carry the meaning too.
 */
export function Stepper({ steps, currentIndex }: StepperProps) {
  return (
    <ol className={styles.stepper} aria-label="Purchase steps">
      {steps.map((step, index) => {
        const isCurrent = index === currentIndex;
        const isDone = index < currentIndex;
        const cls = [styles.step, isCurrent ? styles.current : "", isDone ? styles.done : ""]
          .filter(Boolean)
          .join(" ");
        return (
          <li key={step} className={cls} aria-current={isCurrent ? "step" : undefined}>
            {isDone && (
              <span className={styles.mark} aria-hidden="true">
                ✓
              </span>
            )}
            {step}
            {index < steps.length - 1 && (
              <span className={styles.tick} aria-hidden="true">
                ❦
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
