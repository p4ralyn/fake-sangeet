import { getIngestionTray, usePolled } from "../../lib/opsApi";
import styles from "./Panels.module.css";

/**
 * The sheet rows the ingester could not mint.
 *
 * These have existed in `ingestion_errors` since M3 and had no screen at all —
 * four people with no ticket, in a table nobody could open. Each row shows the
 * normaliser's own view of it, because the fix is to go and repair the sheet
 * and you cannot do that without seeing what was actually in the cell.
 *
 * Nothing here is editable. Repairing the workbook and re-running
 * `scripts/ingest.py` is the fix, and the row hash means only the repaired
 * rows mint. A "resolve" button would let somebody clear the tray without the
 * person on it ever getting a ticket.
 */

function RawRow({ raw }: { raw: Record<string, unknown> | null }) {
  if (!raw) return null;
  const shown = Object.entries(raw).filter(
    ([, value]) =>
      value !== null && value !== "" && !(Array.isArray(value) && value.length === 0),
  );
  return (
    <dl className={styles.raw}>
      {shown.map(([key, value]) => (
        <div key={key} className={styles.rawPair}>
          <dt className={styles.rawKey}>{key.replace(/_/g, " ")}</dt>
          <dd className={styles.rawValue}>{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function IngestionTray() {
  const state = usePolled(getIngestionTray);

  if (state.kind === "loading") return <p className={styles.state}>Loading…</p>;
  if (state.kind === "error") return <p className={styles.errorState}>{state.message}</p>;

  const { unresolved, items } = state.data;

  return (
    <section className={styles.section}>
      <h1 className={styles.title}>Sheet rows</h1>
      {items.length === 0 ? (
        <p className={styles.empty}>
          Every row in the workbooks minted a ticket. Nothing to fix.
        </p>
      ) : (
        <>
          <p className={styles.lede}>
            {unresolved} {unresolved === 1 ? "row" : "rows"} could not be turned into a
            ticket. Fix {unresolved === 1 ? "it" : "them"} in the workbook, then run the
            ingester again — it only mints what changed.
          </p>
          <ul className={styles.rows}>
            {items.map((item) => (
              <li key={item.id} className={styles.row}>
                <p className={styles.rowRef}>{item.row_ref ?? "unknown row"}</p>
                <p className={styles.rowReason}>{item.reason}</p>
                <RawRow raw={item.raw} />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
