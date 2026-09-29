import { ButtonLink, Loading, Notice } from "../components";
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
      <ButtonLink className={styles.scanLink} to="/scan" variant="primary" size="lg" block>
        <ScanGlyph />
        Open the scanner
      </ButtonLink>

      {state.kind === "loading" && <Loading>Loading&hellip;</Loading>}
      {state.kind === "error" && (
        <Notice tone="error">
          <p>{state.message}</p>
        </Notice>
      )}
      {state.kind === "ready" && (
        <>
          <div className={panels.board}>
            <InTheRoom data={state.data} heading="Tonight" />
            <TheDoor data={state.data} />
          </div>
        </>
      )}
    </div>
  );
}

/** Viewfinder brackets round a QR block, in the button's own colour. */
function ScanGlyph() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <path
        d="M2 9V3h6M20 3h6v6M26 19v6h-6M8 25H2v-6"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <rect x="8" y="8" width="5" height="5" fill="currentColor" />
      <rect x="15" y="8" width="5" height="5" fill="currentColor" />
      <rect x="8" y="15" width="5" height="5" fill="currentColor" />
      <rect x="16" y="16" width="3" height="3" fill="currentColor" />
    </svg>
  );
}
