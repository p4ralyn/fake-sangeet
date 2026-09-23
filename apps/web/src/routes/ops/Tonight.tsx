import { getSummary, usePolled } from "../../lib/opsApi";
import type { OpsSummary } from "../../lib/types";
import styles from "./Panels.module.css";

/**
 * The number an OWNER wants at 18:30: how many people are in the room.
 *
 * Everything else on this screen is supporting evidence for one of two
 * questions — is the door working, and is anything backing up. Anything that
 * answers neither is not here.
 */

function Stat({
  value,
  label,
  hint,
  tone,
}: {
  value: number | string;
  label: string;
  hint?: string;
  tone?: "good" | "warn" | "bad";
}) {
  const toneClass =
    tone === "good" ? styles.good : tone === "warn" ? styles.warn : tone === "bad" ? styles.bad : "";
  return (
    <div className={styles.stat}>
      <p className={`${styles.statValue} ${toneClass} tabular-nums`}>{value}</p>
      <p className={styles.statLabel}>{label}</p>
      {hint && <p className={styles.statHint}>{hint}</p>}
    </div>
  );
}

function Board({ data }: { data: OpsSummary }) {
  const { occupancy, seats, queues } = data;
  const pct = seats.total ? Math.round((100 * occupancy.inside) / seats.total) : 0;

  return (
    <>
      <section className={styles.section} aria-label="In the room">
        <h1 className={styles.title}>Tonight</h1>
        <div className={styles.statRow}>
          <Stat
            value={occupancy.inside}
            label="In the room"
            hint={`${pct}% of ${seats.total}`}
            tone={pct >= 90 ? "warn" : "good"}
          />
          <Stat value={occupancy.on_break} label="On a break" />
          <Stat value={occupancy.not_arrived} label="Not arrived" />
          <Stat value={occupancy.departed} label="Left" />
        </div>
      </section>

      <section className={styles.section} aria-label="The door">
        <h2 className={styles.subtitle}>The door</h2>
        <div className={styles.statRow}>
          <Stat
            value={data.scans_recent}
            label={`Scans, last ${data.scans_window_minutes} min`}
            // A gate that has gone quiet at 18:20 is either finished or broken,
            // and the difference is worth knowing before the queue tells you.
            hint={data.scans_recent === 0 ? "Quiet — check the gate is up" : undefined}
            tone={data.scans_recent === 0 ? "warn" : undefined}
          />
          <Stat
            value={data.refusals_recent}
            label="Refused"
            tone={data.refusals_recent > 0 ? "bad" : undefined}
          />
          <Stat
            value={seats.committed}
            label="Seats sold"
            hint={`${seats.total - seats.committed} free of ${seats.total}`}
          />
        </div>
      </section>

      <section className={styles.section} aria-label="Queues">
        <h2 className={styles.subtitle}>Backing up?</h2>
        <div className={styles.statRow}>
          <Stat
            value={queues.ids_waiting}
            label="IDs to review"
            hint={
              queues.ids_oldest_minutes !== null
                ? `oldest ${queues.ids_oldest_minutes} min`
                : undefined
            }
            tone={queues.ids_waiting > 0 ? "warn" : undefined}
          />
          <Stat
            value={queues.ingestion_unresolved}
            label="Sheet rows stuck"
            tone={queues.ingestion_unresolved > 0 ? "warn" : undefined}
          />
          <Stat value={queues.mail_queued} label="Mail queued" />
          <Stat
            value={queues.mail_dead}
            label="Mail gave up"
            tone={queues.mail_dead > 0 ? "bad" : undefined}
          />
        </div>
      </section>
    </>
  );
}

export function Tonight() {
  const state = usePolled(getSummary);

  if (state.kind === "loading") {
    return <p className={styles.state}>Loading…</p>;
  }
  if (state.kind === "error") {
    return <p className={styles.errorState}>{state.message}</p>;
  }
  return <Board data={state.data} />;
}
