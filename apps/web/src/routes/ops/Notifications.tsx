import { Link } from "react-router-dom";
import { getNotifications, usePolled } from "../../lib/opsApi";
import type { Severity } from "../../lib/types";
import styles from "./Panels.module.css";

/**
 * The alert feed.
 *
 * There is no notifications table behind this. Every item is computed from
 * rows that already exist — the review queue, the ingestion tray, the outbox,
 * the scan log — so it cannot drift out of sync with the thing it describes
 * and no service has a write path it can forget. The cost, accepted
 * deliberately: no read/unread, and an item disappears once its condition
 * clears rather than becoming history.
 *
 * An empty feed is the normal state and says so. A board that always has
 * something on it teaches people to stop looking.
 */

const TONE: Record<Severity, string> = {
  CRITICAL: styles.itemCritical,
  WARNING: styles.itemWarning,
  INFO: styles.itemInfo,
};

const LABEL: Record<Severity, string> = {
  CRITICAL: "Needs doing now",
  WARNING: "Worth a look",
  INFO: "For information",
};

export function Notifications() {
  const state = usePolled(getNotifications);

  if (state.kind === "loading") {
    return <p className={styles.state}>Loading…</p>;
  }
  if (state.kind === "error") {
    return <p className={styles.errorState}>{state.message}</p>;
  }

  const { items } = state.data;

  return (
    <section className={styles.section}>
      <h1 className={styles.title}>Alerts</h1>
      {items.length === 0 ? (
        <p className={styles.empty}>
          Nothing needs attention. Reviews are keeping up, mail is going out and the
          sheet has no stuck rows.
        </p>
      ) : (
        <ul className={styles.feed}>
          {items.map((item) => (
            <li key={item.kind} className={`${styles.item} ${TONE[item.severity]}`}>
              <p className={styles.itemTag}>{LABEL[item.severity]}</p>
              <p className={styles.itemTitle}>{item.title}</p>
              <p className={styles.itemDetail}>{item.detail}</p>
              {item.href && (
                <Link className={styles.itemLink} to={item.href}>
                  Go there
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
