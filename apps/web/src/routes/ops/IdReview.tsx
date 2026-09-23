import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { decideReview, getReviewQueue } from "../../lib/verificationApi";
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
    return <p className={styles.state}>Loading the queue…</p>;
  }

  if (!current) {
    return (
      <div className={styles.empty}>
        <h1 className={styles.emptyTitle}>Queue clear</h1>
        <p>No IDs are waiting. New ones appear here as people upload them.</p>
        <button type="button" className={styles.secondary} onClick={() => void load()}>
          Check again
        </button>
      </div>
    );
  }

  return (
    <div className={styles.screen}>
      <header className={styles.header}>
        <span className={styles.count}>
          {pending} waiting
        </span>
        <span className={styles.shortcuts}>{SHORTCUTS}</span>
      </header>

      <figure className={styles.figure}>
        {current.id_image_url ? (
          <img className={styles.photo} src={current.id_image_url} alt="Uploaded student ID" />
        ) : (
          <p className={styles.noPhoto}>
            This image has been purged. Ask them to upload again.
          </p>
        )}
      </figure>

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
          <p className={styles.flag}>
            The same photo was uploaded before. Worth a second look — it is not proof of
            anything on its own.
          </p>
        )}
      </div>

      <input
        className={styles.note}
        value={note}
        placeholder="Note (optional) — they see this if you refuse or ask again"
        onChange={(event) => setNote(event.target.value)}
      />

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.approve}
          disabled={busy}
          onClick={() => void decide(true, false)}
        >
          Approve
        </button>
        <button
          type="button"
          className={styles.secondary}
          disabled={busy}
          onClick={() => void decide(false, true)}
        >
          Ask for another photo
        </button>
        <button
          type="button"
          className={styles.refuse}
          disabled={busy}
          onClick={() => void decide(false, false)}
        >
          Refuse
        </button>
      </div>
    </div>
  );
}
