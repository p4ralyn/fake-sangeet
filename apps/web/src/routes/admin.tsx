/**
 * Route placeholder for the admin console. The admin-console agent replaces
 * this with the real review queue; the route path, guard (OWNER/ADMIN) and
 * component name are the contract this file establishes.
 */
import { Placeholder } from "./Placeholder";

export function AdminPaymentsScreen() {
  return <Placeholder title="Payment review queue" note="GET /api/v1/admin/payments — keyboard-first review." />;
}
