import { getSummary, usePolled } from "../../lib/opsApi";
import type { OpsSummary } from "../../lib/types";
import { InTheRoom, Stat, TheDoor } from "./Boards";
import styles from "./Panels.module.css";

/**
 * The number an OWNER wants at 18:30: how many people are in the room.
 *
 * Everything else on this screen is supporting evidence for one of two
 * questions — is the door working, and is anything backing up. Anything that
 * answers neither is not here.
 *
 * The first two boards are shared with the gate screen (see Boards.tsx). The
 * queue board below is NOT: it is the only part of this screen that is about
 * staff work rather than the room, and a volunteer has no use for it.
 */

function Board({ data }: { data: OpsSummary }) {
  const { queues } = data;

  return (
    <>
      <InTheRoom data={data} heading="Tonight" />
      <TheDoor data={data} />

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
