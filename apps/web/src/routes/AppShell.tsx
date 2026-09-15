import { Outlet, useLocation } from "react-router-dom";
import { Button } from "../components/Button";
import { useSession } from "../lib/session";
import styles from "./AppShell.module.css";

/**
 * The app shell: event name, signed-in identity, sign-out control. Content
 * is centred at 34rem on participant screens and goes wide under /admin,
 * per the design spec's layout rule.
 */
export function AppShell() {
  const { user, signOut } = useSession();
  const location = useLocation();
  const isAdmin = location.pathname.startsWith("/admin");

  return (
    <>
      <header className={styles.header}>
        <span className={styles.eventName}>{user?.event.name ?? "unigate"}</span>
        {user && (
          <div className={styles.identity}>
            <span>{user.display_name}</span>
            <span className={styles.role}>{user.roles.join(", ")}</span>
            <Button variant="quiet" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        )}
      </header>
      <main className={isAdmin ? styles.mainWide : styles.mainNarrow}>
        <Outlet />
      </main>
    </>
  );
}
