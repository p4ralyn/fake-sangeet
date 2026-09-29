import { NavLink, Outlet } from "react-router-dom";
import { buttonClass } from "../../components";
import { useSession } from "../../lib/session";
import styles from "./OpsShell.module.css";

/**
 * The staff area (`/ops`).
 *
 * A rail cut from the reel: sprockets down both edges, one frame per
 * destination, the lit frame is where you are.
 *
 * A rail, not a header: three destinations that people move between all night,
 * where a header would push the work down the page on the laptop this is
 * actually used on. The shape follows Primus IQ's workspace pattern, in this
 * app's own palette.
 *
 * It exists because staff navigation did not. An ADMIN on the review queue had
 * no link to anything and had to know URLs.
 *
 * It used to carry five. **Alerts** went because every fact it derived — mail
 * dead, mail queued, IDs waiting and their age, sheet rows stuck, refusals,
 * occupancy — is already a number on Tonight, so it was a second rendering of
 * one screen and a second place to keep in sync. **Sheet rows** went with it:
 * a stuck row is fixed in the spreadsheet, and `scripts/ingest.py --dry-run`
 * names every failing row and why, which is the tool for that desk. Tonight
 * still shows the count, so nobody has to remember to look.
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
  { to: "/ops/mail", label: "Mail" },
];

export function OpsShell() {
  const { user } = useSession();

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
          </NavLink>
        ))}

        <a className={buttonClass({ variant: "marigold", block: true }, styles.scannerLink)} href="/scan">
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
          <span>
            {user?.net_id}
            <br />
            <span className={styles.role}>{user?.roles.join(", ")}</span>
          </span>
        </p>
      </nav>

      <div className={styles.panel}>
        <Outlet />
      </div>
    </div>
  );
}
