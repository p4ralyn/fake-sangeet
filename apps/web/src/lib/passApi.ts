/**
 * The pass endpoints. Real API — there is no mock adapter any more.
 *
 * `mock.ts` is gone from this branch along with the rest of the booking flow;
 * it lives on `feature/booking-flow` for whenever phase-3 selling comes back.
 * What is left is a security ticketing app: who holds a pass, is their ID
 * checked, and does the door let them in.
 */
import { api } from "./api";
import type { MyPassResponse, TransferResponse } from "./types";

export function getMyPass(): Promise<MyPassResponse> {
  return api.get<MyPassResponse>("/api/v1/me/pass");
}

/**
 * Hand this person's ticket to somebody else. Which ticket moves is derived
 * from the session, so there is nothing to pass but the recipient.
 *
 * Closes three hours before doors — the server refuses after that, and
 * `MyPassResponse.transfer` says when so the screen can stop offering it.
 */
export function transferPass(toNetId: string): Promise<TransferResponse> {
  return api.post<TransferResponse>("/api/v1/me/pass/transfer", {
    to_net_id: toNetId,
  });
}
