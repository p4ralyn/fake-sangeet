import { useCallback, useEffect, useRef, useState } from "react";
import { Button, EmptyState, Loading, Notice, Paper, Stamp } from "../../components";
import { ApiError } from "../../lib/api";
import {
  HIDDEN_REFRESH_MS,
  REVIEW_REFRESH_MS,
  decideReview,
  getReviewQueue,
  mergeReviewQueue,
} from "../../lib/verificationApi";
import type { ReviewItem } from "../../lib/types";
import styles from "./IdReview.module.css";

/**
 * The ID review queue (M4). One card at a time, not a table.
 *
 * The shape follows the job: somebody has to look at up to 600 photographs and
 * answer one question about each — is this person's ID. A table of thumbnails
 * optimises for scanning a list, which is the wrong thing; the photo needs to
 * be big enough to read a name off. So: one card, keyboard-driven, and the
 * next one arrives the moment this one is decided.
 *
 * Keys are the whole point of the design. A reviewer working a queue this long
 * should never touch the mouse: A approves, R asks for another photo, X
 * refuses outright. The buttons exist for the first five minutes and for
 * anyone who prefers them.
 *
 * Signed image URLs are ~90 seconds old by the time a slow reviewer reaches
 * the bottom of a page, so the queue reloads rather than paginating in place.
 */

const SHORTCUTS = "A approves · R asks for another photo · X refuses";

export function IdReview() {
  const [queue, setQueue] = useState<ReviewItem[]>([]);
  const [pending, setPending] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [movedOn, setMovedOn] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getReviewQueue();
      setQueue(result.items);
      setPending(result.pending);
      setCursor(0);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Couldn’t load the queue.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The live part. Several admins work this queue at once, so it re-checks:
  // new uploads appear without a reload, and cards someone else decided drop
  // out before anybody wastes a look on them. Every 5s while the tab is in
  // front; every 30s behind other tabs, which is enough to keep the "(N) ID
  // review" title honest -- that title is the alert for a background tab, so
  // pausing there would defeat it -- and at once when the tab comes back.
  // The latest state is read through a ref so the interval is armed once. A
  // tick is skipped while a decision is in flight. The server still has the
  // last word: deciding a card decided a moment ago gets a 409, never a
  // second outcome.
  const live = useRef({ queue, cursor, busy, loading });
  live.current = { queue, cursor, busy, loading };
  useEffect(() => {
    let cancelled = false;
    let lastFetch = 0;
    const tick = async (force = false) => {
      const now = live.current;
      if (now.busy || now.loading) return;
      const every = document.visibilityState === "hidden" ? HIDDEN_REFRESH_MS : REVIEW_REFRESH_MS;
      if (!force && Date.now() - lastFetch < every - 250) return;
      lastFetch = Date.now();
      try {
        const fresh = await getReviewQueue();
        if (cancelled || live.current.busy) return;
        const merged = mergeReviewQueue(live.current.queue, live.current.cursor, fresh.items);
        setQueue(merged.queue);
        setCursor(merged.cursor);
        setPending(fresh.pending);
        if (merged.decidedElsewhere) {
          setNote("");
          setMovedOn(merged.decidedElsewhere.full_name);
        }
      } catch {
        // A missed tick is not an error worth showing; the next one retries.
      }
    };
    const timer = window.setInterval(() => void tick(), REVIEW_REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  // "Someone else decided X" is news for a moment, not a fixture: left up, it
  // ends up sitting over a card that arrived later and has nothing to do with it.
  useEffect(() => {
    if (!movedOn) return;
    const timer = window.setTimeout(() => setMovedOn(null), 8000);
    return () => window.clearTimeout(timer);
  }, [movedOn]);

  // A background tab says when there is work, without anyone looking at it.
  useEffect(() => {
    const before = document.title;
    document.title = pending > 0 ? `(${pending}) ID review · Fake Sangeet` : "ID review · Fake Sangeet";
    return () => {
      document.title = before;
    };
  }, [pending]);

  const current = queue[cursor];

  const decide = useCallback(
    async (approve: boolean, allowResubmit: boolean) => {
      if (!current || busy) return;
      setBusy(true);
      setError(null);
      try {
        await decideReview(current.verification_id, {
          approve,
          note: note.trim() || null,
          allow_resubmit: allowResubmit,
        });
        setNote("");
        setMovedOn(null);
        setPending((count) => Math.max(0, count - 1));
        // Advance locally rather than refetching per decision: a reload
        // between every card would put a network round trip between the
        // reviewer and the next photograph.
        if (cursor + 1 >= queue.length) {
          await load();
        } else {
          setCursor((index) => index + 1);
        }
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : "That decision didn’t save. Try again.",
        );
        // A 409 means somebody else got there first — the page is stale.
        if (caught instanceof ApiError && caught.status === 409) {
          await load();
        }
      } finally {
        setBusy(false);
      }
    },
    [busy, current, cursor, load, note, queue.length],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      // Never steal a key from the note field.
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      const key = event.key.toLowerCase();
      if (key === "a") void decide(true, false);
      if (key === "r") void decide(false, true);
      if (key === "x") void decide(false, false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide]);

  if (loading) {
    return <Loading>Loading the queue…</Loading>;
  }

  if (!current) {
    return (
      <EmptyState
        title="Queue clear"
        action={
          <Button variant="secondary" onClick={() => void load()}>
            Check again
          </Button>
        }
      >
        {movedOn && (
          <Notice tone="info">
            <p>Someone else just decided {movedOn}&rsquo;s ID.</p>
          </Notice>
        )}
        <p>No IDs are waiting. New ones appear here as people upload them.</p>
      </EmptyState>
    );
  }

  return (
    <div className={styles.screen}>
      <header className={styles.header}>
        <Stamp tone={pending > 0 ? "warn" : "neutral"} glyph="◔" className={styles.count}>
          {pending} waiting
        </Stamp>
        <span className={styles.shortcuts}>{SHORTCUTS}</span>
      </header>

      <Paper as="div" lift="md" tape pad="sm" className={styles.figure}>
        {current.id_image_url ? (
          <img className={styles.photo} src={current.id_image_url} alt="Uploaded student ID" />
        ) : (
          <p className={styles.noPhoto}>
            This image has been purged. Ask them to upload again.
          </p>
        )}
      </Paper>

      <div className={styles.identity}>
        <h1 className={styles.name}>{current.full_name}</h1>
        <p className={styles.meta}>
          {/* The NetID is dropped when it IS the name — which happens whenever
              the identity provider gave us nothing better. Printing "Pk944"
              over "pk944" reads as a rendering bug. */}
          {current.full_name.toLowerCase() !== current.net_id.toLowerCase()
            ? current.net_id
            : ""}
          {current.attempt_no > 1
            ? `${current.full_name.toLowerCase() !== current.net_id.toLowerCase() ? " — " : ""}attempt ${current.attempt_no}`
            : ""}
        </p>
        {current.fraud_flags.includes("DUPLICATE_IMAGE") && (
          <Notice tone="warn" className={styles.flag}>
            <p>
              The same photo was uploaded before. Worth a second look — it is not proof of
              anything on its own.
            </p>
          </Notice>
        )}
      </div>

      <label className="sr-only" htmlFor="review-note">
        Note
      </label>
      <input
        id="review-note"
        className={styles.note}
        value={note}
        placeholder="Note (optional) — they see this if you refuse or ask again"
        onChange={(event) => setNote(event.target.value)}
      />

      {(movedOn || error) && (
        <div className={styles.error}>
          {movedOn && (
            <Notice tone="info" live>
              <p>
                Someone else just decided {movedOn}&rsquo;s ID, so you&rsquo;ve moved on to the
                next one.
              </p>
            </Notice>
          )}
          {error && (
            <Notice tone="error" live>
              <p>{error}</p>
            </Notice>
          )}
        </div>
      )}

      <div className={styles.actions}>
        <Button variant="good" size="lg" block disabled={busy} onClick={() => void decide(true, false)}>
          Approve
          <kbd className={styles.key} aria-hidden="true">A</kbd>
        </Button>
        <Button variant="secondary" size="lg" block disabled={busy} onClick={() => void decide(false, true)}>
          Ask for another photo
          <kbd className={styles.key} aria-hidden="true">R</kbd>
        </Button>
        <Button variant="danger" size="lg" block disabled={busy} onClick={() => void decide(false, false)}>
          Refuse
          <kbd className={styles.key} aria-hidden="true">X</kbd>
        </Button>
      </div>
    </div>
  );
}
