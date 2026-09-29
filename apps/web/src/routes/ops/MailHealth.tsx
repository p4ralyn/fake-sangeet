import { getMailHealth, usePolled } from "../../lib/opsApi";
import { DisplayTitle, Loading, Notice, Paper, Stamp, Stat, StatGrid } from "../../components";
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

  if (state.kind === "loading") return <Loading>Loading…</Loading>;
  if (state.kind === "error")
    return (
      <Notice tone="error">
        <p>{state.message}</p>
      </Notice>
    );

  const mail = state.data;

  return (
    <section className={styles.section}>
      <DisplayTitle tone="ink" className={styles.title}>
        Mail
      </DisplayTitle>

      <StatGrid>
        <Stat value={mail.sent} label="Sent" tone="good" />
        <Stat value={mail.queued} label="Waiting" />
        <Stat value={mail.dead} label="Gave up" tone={mail.dead ? "bad" : undefined} />
      </StatGrid>

      {mail.queued > 0 && (
        <Notice tone="wait">
          <p>
            Waiting mail goes out when the worker runs:{" "}
            <code className={styles.code}>scripts/mailer.py</code>.
          </p>
        </Notice>
      )}

      {mail.problems.length === 0 ? (
        <Notice tone="good">
          <p>Nothing has failed. Every message either went or is queued.</p>
        </Notice>
      ) : (
        <ul className={styles.rows}>
          {mail.problems.map((problem) => (
            <li key={problem.id}>
              <Paper pad="sm" className={styles.row}>
                <p className={styles.rowRef}>{problem.to_email}</p>
                <Stamp
                  size="sm"
                  tone={problem.status === "DEAD" ? "bad" : "warn"}
                  glyph={problem.status === "DEAD" ? "✕" : "↻"}
                  className={styles.rowReason}
                >
                  {problem.status === "DEAD"
                    ? `Gave up after ${problem.attempts} tries`
                    : `Failed ${problem.attempts}× — will try again`}
                </Stamp>
                {problem.last_error && (
                  <p className={styles.rowError}>{problem.last_error}</p>
                )}
              </Paper>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
