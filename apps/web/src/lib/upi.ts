// -----------------------------------------------------------------------------
// UPI collection accounts.
//
// The VPA list is DERIVED FROM THE FILENAMES in `src/assets/upi-qr/` — each
// file is named for the VPA its QR encodes. Adding an account is therefore one
// step: drop `<vpa>.jpeg` in that folder. Nothing here needs editing, and the
// list cannot drift from the images the way a hardcoded array would.
//
// WHY ROTATE: a personal VPA taking a few hundred payments in one evening is
// the pattern banks flag. Spreading across accounts reduces the per-account
// volume. It does NOT eliminate the risk — see the note at the bottom.
//
// WHERE THIS LIVES: rotation must be decided SERVER-side. A client cannot know
// the global approved-payment count, and two buyers on the payment page at the
// same moment must be told the same thing. The API already returns `upi_vpa`
// and `upi_qr_url` on `GET /payments/{id}/page`; the UI renders whatever it is
// given. This module is the mock's data source until M5 implements it for real,
// at which point the images move to the backend and this file is deleted.
// -----------------------------------------------------------------------------

const qrModules = import.meta.glob("../assets/upi-qr/*.{jpeg,jpg,png}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

export interface UpiAccount {
  /** The VPA itself, e.g. `someone@okhdfcbank`. Taken from the filename. */
  vpa: string;
  /** Bundled URL of the QR image for that VPA. */
  qrUrl: string;
}

/** Sorted so rotation order is stable across builds and machines. */
export const UPI_ACCOUNTS: UpiAccount[] = Object.entries(qrModules)
  .map(([path, qrUrl]) => ({
    vpa: path.split("/").pop()!.replace(/\.(jpeg|jpg|png)$/i, ""),
    qrUrl,
  }))
  .sort((a, b) => a.vpa.localeCompare(b.vpa));

/** Successful payments taken on one account before moving to the next. */
export const ROTATE_EVERY_N_PAYMENTS = 20;

/**
 * Which account a buyer should pay, given how many payments have already been
 * approved for this event.
 *
 * Deliberately a pure function of the count: the same count always yields the
 * same account, so the choice is reproducible when reconciling later.
 */
export function accountForApprovedCount(approvedCount: number): UpiAccount {
  if (UPI_ACCOUNTS.length === 0) {
    throw new Error(
      "No UPI QR images found. Add `<vpa>.jpeg` files to src/assets/upi-qr/.",
    );
  }
  const block = Math.floor(Math.max(0, approvedCount) / ROTATE_EVERY_N_PAYMENTS);
  return UPI_ACCOUNTS[block % UPI_ACCOUNTS.length];
}

// -----------------------------------------------------------------------------
// Operational note, recorded here because it is easy to forget once this works:
//
// Rotation changes the ORDER payments land in, not the TOTAL per account. With
// 2 accounts and ~150 seats you still put ~75 payments (~Rs 75,000 at the couple
// price) through each personal account in a day — which is the volume that
// triggers a bank review in the first place. Rotation buys headroom; a current
// account is the actual fix. LLD-v2 section 15, open item 2.
//
// It also makes reconciliation harder: the bank CSV import must handle N
// statements, and every approved payment must be matched against the account it
// was actually routed to, not a single assumed one.
// -----------------------------------------------------------------------------
