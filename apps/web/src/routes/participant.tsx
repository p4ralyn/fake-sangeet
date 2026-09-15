/**
 * Route placeholders for the participant purchase flow (addendum §A2).
 * The next agent replaces each of these with the real screen; the route
 * path, guard and component name are the contract this file establishes.
 */
import { Placeholder } from "./Placeholder";

export function BuyTicketTypeScreen() {
  return <Placeholder title="Choose your seat" note="Step 1 — ticket type. Built by the participant-screens agent." />;
}

export function BuyDetailsScreen() {
  return <Placeholder title="Your details" note="Step 2 — details form, granted a hold on submit." />;
}

export function PaymentScreen() {
  return <Placeholder title="Complete payment" note="Step 3 — only reachable with a live hold you own." />;
}

export function PassScreen() {
  return <Placeholder title="Your pass" note="GET /my/pass — the tear-off card with the QR." />;
}
