import { useEffect, useRef } from "react";

/**
 * Minimal shape of the Screen Wake Lock API. Declared locally rather than
 * relying on lib.dom having it (support varies by TS/lib version) — this is
 * the full surface this hook touches, nothing more.
 */
interface WakeLockSentinelLike {
  release: () => Promise<void>;
}
interface WakeLockLike {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
}

function getWakeLock(): WakeLockLike | null {
  const nav = navigator as Navigator & { wakeLock?: WakeLockLike };
  return nav.wakeLock ?? null;
}

/**
 * Keeps the screen awake while `active` is true — a volunteer holding this
 * screen for three hours cannot be re-tapping it to stop it sleeping.
 * Reacquires on tab visibility change, since the OS releases the lock
 * whenever the tab is backgrounded (§11.5, "screen-wake lock"). Unsupported
 * browsers (no Wake Lock API) simply get no lock — scanning still works.
 */
export function useWakeLock(active: boolean): void {
  const sentinelRef = useRef<WakeLockSentinelLike | null>(null);

  useEffect(() => {
    const wakeLock = getWakeLock();
    if (!active || !wakeLock) {
      return;
    }
    let cancelled = false;

    async function acquire() {
      try {
        const sentinel = await wakeLock!.request("screen");
        if (cancelled) {
          void sentinel.release();
          return;
        }
        sentinelRef.current = sentinel;
      } catch {
        // Refusable (e.g. low battery) — the screen just might dim, scanning
        // itself is unaffected.
      }
    }

    void acquire();

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        void acquire();
      }
    }
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      const sentinel = sentinelRef.current;
      sentinelRef.current = null;
      void sentinel?.release();
    };
  }, [active]);
}
