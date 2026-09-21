import { useEffect, useState } from "react";

/**
 * Drives the header's online/offline pill. This is a display seam only —
 * offline *scanning* is out of scope here (it needs a server-issued,
 * gate-scoped verification key, which is backend work). Going offline just
 * means the next scan attempt will fail with a network error and the
 * operator sees that plainly, rather than the app pretending to be online.
 */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(() => (typeof navigator === "undefined" ? true : navigator.onLine));

  useEffect(() => {
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return online;
}
