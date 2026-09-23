/**
 * The ops dashboard endpoints (ADMIN/OWNER).
 *
 * Polled, not streamed. LLD-v1 §19 specifies SSE for the live dashboard,
 * citing the Primus IQ `EventSource` hook — worth doing if this ever feels
 * laggy, but for a four-hour event a ten-second poll is indistinguishable and
 * costs no reconnect logic, no backoff hook and no long-lived connection
 * through Caddy. Start simple; the seam is one hook wide.
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import type {
  MailHealth,
  OpsSummary,
} from "./types";

/** How often the live panels refetch. */
export const POLL_MS = 10_000;

export const getSummary = () => api.get<OpsSummary>("/api/v1/ops/summary");
export const getMailHealth = () => api.get<MailHealth>("/api/v1/ops/mail");

export type Loaded<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error"; message: string };

/**
 * Fetch once, then every `POLL_MS` while mounted.
 *
 * A failed poll keeps the last good data on screen rather than blanking the
 * panel: a dashboard that flashes empty every time the wifi hiccups is worse
 * than one that is briefly ten seconds stale.
 */
export function usePolled<T>(fetcher: () => Promise<T>, poll = POLL_MS): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ kind: "loading" });
  const load = useCallback(async () => {
    try {
      const data = await fetcher();
      setState({ kind: "ready", data });
    } catch (error) {
      setState((previous) =>
        previous.kind === "ready"
          ? previous
          : {
              kind: "error",
              message:
                error instanceof ApiError ? error.message : "Couldn’t reach the server.",
            },
      );
    }
    // The fetcher is a module-level function in every call site, so it is
    // stable; listing it would only re-arm the interval on each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), poll);
    return () => window.clearInterval(timer);
  }, [load, poll]);

  return state;
}
