import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../../lib/session";
import { getNotifications, usePolled } from "../../lib/opsApi";
import styles from "./OpsShell.module.css";

/**
 * The staff area (`/ops`).
 *
 * A rail, not a header: five destinations that people move between all night,
 * where a header would push the work down the page on the laptop this is
 * actually used on. The shape follows Primus IQ's workspace pattern, in this
 * app's own palette.
 *
 * It exists because staff navigation did not. An ADMIN on the review queue had
 * no link to anything and had to know URLs — and two things the system already
 * recorded, the ingestion tray and the mail outbox, had no screen at all.
 *
 * Participant screens deliberately do NOT get this chrome: three screens do not
 * need a rail, and nav furniture around somebody's pass makes the pass worse.
 * `/scan` stays outside it too — it is full-bleed by design.
 */

interface Destination {
  to: string;
  label: string;
  end?: boolean;
}

const DESTINATIONS: Destination[] = [
  { to: "/ops", label: "Tonight", end: true },
  { to: "/ops/ids", label: "ID review" },
  { to: "/ops/ingestion", label: "Sheet rows" },
  { to: "/ops/mail", label: "Mail" },
  { to: "/ops/notifications", label: "Alerts" },
];

export function OpsShell() {
  const { user } = useSession();
  // The badge is the reason the rail is worth having: it is how somebody
  // notices a dead email from the ID queue. Polled with everything else.
  const feed = usePolled(getNotifications);
  const alerts = feed.kind === "ready" ? feed.data.items.length : 0;
  const loudest =
    feed.kind === "ready"
      ? feed.data.items.find((item) => item.severity === "CRITICAL")
      : undefined;

  return (
    <div className={styles.ops}>
      <nav className={styles.rail} aria-label="Ops">
        <p className={styles.railTitle}>Ops</p>
        {DESTINATIONS.map((destination) => (
          <NavLink
            key={destination.to}
            to={destination.to}
            end={destination.end}
            className={({ isActive }) =>
              isActive ? `${styles.link} ${styles.linkActive}` : styles.link
            }
          >
            {destination.label}
            {destination.label === "Alerts" && alerts > 0 && (
              <span className={loudest ? styles.badgeLoud : styles.badge}>{alerts}</span>
            )}
          </NavLink>
        ))}

        <a className={styles.scannerLink} href="/scan">
          Open the scanner
        </a>
        <p className={styles.who}>
          {user?.avatar_url && (
            <img
              className={styles.avatar}
              src={user.avatar_url}
              alt=""
              aria-hidden="true"
              referrerPolicy="no-referrer"
            />
          )}
          {user?.net_id}
          <br />
          <span className={styles.role}>{user?.roles.join(", ")}</span>
        </p>
      </nav>

      <div className={styles.panel}>
        <Outlet />
      </div>
    </div>
  );
}
