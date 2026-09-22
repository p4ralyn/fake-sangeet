/**
 * The M4 endpoints. Real API, not `mock.ts` — ID upload and review are shipped
 * backend, unlike the commerce screens.
 *
 * `useMyVerification` never throws. A person with no ticket gets a 404 from
 * the server, which is an answer ("you don't hold one"), not a failure, and
 * the landing page has to be able to ask without a try/catch around it.
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import type {
  MyVerification,
  ReviewDecisionRequest,
  ReviewItem,
  ReviewQueue,
} from "./types";

export function getMyVerification(): Promise<MyVerification> {
  return api.get<MyVerification>("/api/v1/me/verification");
}

/**
 * Multipart, because the API takes the bytes rather than issuing a signed
 * upload URL — see the note at the top of the route module. `api.post` passes
 * FormData through untouched and still attaches the CSRF header.
 */
export function uploadIdImage(file: File, { consent }: { consent: boolean }) {
  const body = new FormData();
  body.append("image", file);
  body.append("consent", String(consent));
  return api.post<MyVerification>("/api/v1/me/id-image", body);
}

export function getReviewQueue(limit = 50): Promise<ReviewQueue> {
  return api.get<ReviewQueue>(`/api/v1/admin/verifications?limit=${limit}`);
}

export function decideReview(
  verificationId: string,
  decision: ReviewDecisionRequest,
): Promise<ReviewItem> {
  return api.post<ReviewItem>(
    `/api/v1/admin/verifications/${verificationId}/decide`,
    decision,
  );
}

export type MyVerificationState =
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "ready"; verification: MyVerification }
  | { kind: "error"; message: string };

export function useMyVerification(): MyVerificationState & { reload: () => void } {
  const [state, setState] = useState<MyVerificationState>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    getMyVerification()
      .then((verification) => {
        if (!cancelled) setState({ kind: "ready", verification });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // 404 means "no ticket for this account" — a fact about this person,
        // not an error to shout about. 401 means signed out, which the
        // session context already handles.
        if (error instanceof ApiError && (error.status === 404 || error.status === 401)) {
          setState({ kind: "none" });
          return;
        }
        setState({
          kind: "error",
          message:
            error instanceof ApiError
              ? error.message
              : "We couldn’t check your ID status. Try again in a moment.",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [nonce]);

  return { ...state, reload };
}
