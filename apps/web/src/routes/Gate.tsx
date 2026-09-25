import { Link } from "react-router-dom";
import { InTheRoom, TheDoor } from "./ops/Boards";
import { getSummary, usePolled } from "../lib/opsApi";
import panels from "./ops/Panels.module.css";
import styles from "./Gate.module.css";

/**
 * What a SECURITY volunteer sees (`/gate`).
 *
 * Two boards and a button. The boards are the same components the ops
 * dashboard draws, so the count a volunteer reads and the count an owner reads
 * can never disagree; the button is the job.
 *
 * **No rail.** /ops has one because it has somewhere to go; this screen has a
 * single destination and a rail listing places a volunteer cannot enter would
 * be furniture that only advertises what they are missing.
 *
 * **No queue board either**, and that is the line rather than an oversight:
 * counts describe the room, while the queues name people who are waiting or
 * whose mail failed. `/api/v1/ops/summary` is open to SECURITY for exactly
 * this reason and `/api/v1/ops/mail` is not (LLD-v1 §11.5).
 */
export function Gate() {
  const state = usePolled(getSummary);

  return (
    <div className={styles.gate}>
      <Link className={styles.scanLink} to="/scan">
        Open the scanner
      </Link>

      {state.kind === "loading" && <p className={panels.state}>Loading&hellip;</p>}
      {state.kind === "error" && <p className={panels.errorState}>{state.message}</p>}
      {state.kind === "ready" && (
        <>
          <InTheRoom data={state.data} heading="Tonight" />
          <TheDoor data={state.data} />
        </>
      )}
    </div>
  );
}
