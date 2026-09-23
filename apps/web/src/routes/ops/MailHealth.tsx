import { getMailHealth, usePolled } from "../../lib/opsApi";
import styles from "./Panels.module.css";

/**
 * Outbox health.
 *
 * The question this answers is "who didn't get theirs", which is why the
 * addresses are shown — this screen is ADMIN/OWNER and shows no more than the
 * review queue already does.
 *
 * A row sitting in QUEUED having never been tried is a queue, not a problem,
 * so it is counted rather than listed. Listing it would bury the rows that
 * actually failed.
 */
export function MailHealth() {
  const state = usePolled(getMailHealth);

  if (state.kind === "loading") return <p className={styles.state}>Loading…</p>;
  if (state.kind === "error") return <p className={styles.errorState}>{state.message}</p>;

  const mail = state.data;

  return (
    <section className={styles.section}>
      <h1 className={styles.title}>Mail</h1>

      <div className={styles.statRow}>
        <div className={styles.stat}>
          <p className={`${styles.statValue} ${styles.good} tabular-nums`}>{mail.sent}</p>
          <p className={styles.statLabel}>Sent</p>
        </div>
        <div className={styles.stat}>
          <p className={`${styles.statValue} tabular-nums`}>{mail.queued}</p>
          <p className={styles.statLabel}>Waiting</p>
        </div>
        <div className={styles.stat}>
          <p className={`${styles.statValue} ${mail.dead ? styles.bad : ""} tabular-nums`}>
            {mail.dead}
          </p>
          <p className={styles.statLabel}>Gave up</p>
        </div>
      </div>

      {mail.queued > 0 && (
        <p className={styles.lede}>
          Waiting mail goes out when the worker runs:{" "}
          <code className={styles.code}>scripts/mailer.py</code>.
        </p>
      )}

      {mail.problems.length === 0 ? (
        <p className={styles.empty}>
          Nothing has failed. Every message either went or is queued.
        </p>
      ) : (
        <ul className={styles.rows}>
          {mail.problems.map((problem) => (
            <li key={problem.id} className={styles.row}>
              <p className={styles.rowRef}>{problem.to_email}</p>
              <p className={styles.rowReason}>
                {problem.status === "DEAD"
                  ? `Gave up after ${problem.attempts} tries`
                  : `Failed ${problem.attempts}× — will try again`}
              </p>
              {problem.last_error && (
                <p className={styles.rowError}>{problem.last_error}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
