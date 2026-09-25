import { Link, Outlet, useLocation } from "react-router-dom";
import { Button } from "../components/Button";
import { MandalaCorner } from "../components/MandalaCorner";
import { isGateStaff, isOpsStaff } from "../lib/home";
import { useSession } from "../lib/session";
import styles from "./AppShell.module.css";

/**
 * The SNUPHORIA mark, inlined (not an <img src>) so its `currentColor`
 * fills actually pick up the CSS `color` set on it — an externally
 * referenced SVG cannot inherit page colour, only an inline one can.
 * Carries its own accessible name (a real <title> + role="img") per the
 * design spec's "both logos need real alt text naming the society".
 */
function SnuphoriaMark() {
  return (
    <svg className={styles.logo} viewBox="0 0 307 297" fill="none" role="img" aria-label="Snuphoria" focusable="false">
      <title>Snuphoria, SNU&rsquo;s music society</title>
      <rect x="62" y="42" width="15" height="244" rx="7.5" fill="currentColor" />
      <rect x="29" y="93" width="15" height="154" rx="7.5" fill="currentColor" />
      <rect y="140" width="11" height="47" rx="5.5" fill="currentColor" />
      <rect width="15" height="244" rx="7.5" transform="matrix(-1 0 0 1 245 42)" fill="currentColor" />
      <rect width="15" height="154" rx="7.5" transform="matrix(-1 0 0 1 278 93)" fill="currentColor" />
      <rect width="11" height="47" rx="5.5" transform="matrix(-1 0 0 1 307 140)" fill="currentColor" />
      <path
        d="M197.15 127.517H166.218V100.451C166.218 84.9846 164.837 82.499 152.685 82.499C140.533 82.499 139.152 84.9846 139.152 100.451V121.993C139.152 130.831 140.256 133.869 147.437 138.288C153.513 142.155 164.837 147.126 177.265 154.859C196.046 166.459 198.255 172.259 198.255 196.563V231.639C198.255 271.962 190.522 277.762 152.961 277.762C117.057 277.762 108.495 272.514 108.495 231.639V188.278H139.428V231.639C139.428 246.829 141.085 249.591 152.685 249.591C164.561 249.591 166.218 246.829 166.218 231.639V198.773C166.218 187.173 165.665 183.307 158.761 178.611C151.304 173.364 135.285 165.631 124.79 158.726C109.324 148.507 107.39 141.879 107.39 121.441V100.451C107.39 59.8518 114.847 54.0519 152.132 54.0519C189.417 54.0519 197.15 59.8518 197.15 100.451V127.517Z"
        fill="currentColor"
      />
    </svg>
  );
}

/**
 * The app shell: the collab lockup (Snuphoria x Inferno), the event
 * wordmark, the signed-in identity, and the four mandala corner rosettes
 * that anchor every screen (docs/design/frontend-design-spec.md, "Header"
 * and "Ornament"). Content is centred at 34rem on participant screens and
 * goes wide under /admin, per the design spec's layout rule.
 */
export function AppShell() {
  const { user, roles, signOut } = useSession();
  const location = useLocation();
  // The staff area needs the wide column; participant screens keep the 34rem
  // one. /admin is kept because its redirects render for a frame.
  const isStaffArea =
    location.pathname.startsWith("/ops") || location.pathname.startsWith("/admin");
  // /gate is not the staff AREA -- it has no rail and SECURITY cannot enter
  // /ops -- but it draws the same stat grids, and four stats in a 34rem column
  // wrap three-and-one. Width is a layout question, not a permission one.
  const wantsWideColumn = isStaffArea || location.pathname.startsWith("/gate");
  // Derived from the roles the SERVER put in the session, never a client-side
  // flag, and from one shared definition rather than a copy per component.
  // These only decide whether a link is drawn: /ops and /scan are guarded by
  // RequireRole and every endpoint behind them by require_role, so drawing one
  // for the wrong person would be untidy rather than unsafe.
  const isStaff = isOpsStaff(roles);
  const isGate = isGateStaff(roles);

  return (
    <>
      <MandalaCorner corner="top-left" />
      <MandalaCorner corner="top-right" />
      <MandalaCorner corner="bottom-left" />
      <MandalaCorner corner="bottom-right" />

      <header className={styles.header}>
        <Link to="/" className={styles.brand} aria-label="Fake Sangeet — home">
          <SnuphoriaMark />
          <span className={styles.collabX} aria-hidden="true">
            &times;
          </span>
          <img
            className={styles.infernoLogo}
            src="/logo/inferno.png"
            alt="Inferno, SNU's dance society"
          />
        </Link>

        <div className={styles.wordmarkBlock}>
          {/* The event's own wordmark, replacing the type treatment that stood
              in for it. On the maroon plaque its marigold fill reads properly;
              bare on the saffron ground it all but disappeared. */}
          <span className={styles.wordmarkPlaque}>
            <img className={styles.wordmark} src="/logo/event.svg" alt="Fake Sangeet" />
          </span>
          <p className={styles.tagline}>band baaja no baarat</p>
        </div>

        {user && (
          <div className={styles.identity}>
            {user.avatar_url && (
              <img
                className={styles.avatar}
                src={user.avatar_url}
                alt=""
                // Decorative: the name is right beside it, so announcing the
                // picture as well would just say the name twice.
                aria-hidden="true"
                referrerPolicy="no-referrer"
              />
            )}
            <span className={styles.name}>{user.display_name}</span>
            <span className={styles.role}>{user.roles.join(", ")}</span>
            {isGate && !isStaff && (
              // SECURITY's whole staff surface. They walk the participant
              // screens for their own pass like anybody else, so without this
              // the door screen is reachable only by typing the URL — and they
              // get this ONE link, never the rest of /ops (LLD-v1 §11.5).
              // Hidden for ops staff, who already have Dashboard and would
              // otherwise carry two links to overlapping places.
              <Link className={styles.staffLink} to="/gate">
                Gate
              </Link>
            )}
            {isStaff && !isStaffArea && (
              // The way INTO /ops. Without this a staff member lands on a
              // participant screen with no route to the dashboard except
              // typing the URL — a rail inside /ops is no use to somebody who
              // cannot get there. Hidden once inside, where the rail navigates.
              <Link className={styles.staffLink} to="/ops">
                Dashboard
              </Link>
            )}
            <Button variant="quiet" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        )}
      </header>
      <main className={wantsWideColumn ? styles.mainWide : styles.mainNarrow}>
        <Outlet />
      </main>
    </>
  );
}
