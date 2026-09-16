import { Route, Routes } from "react-router-dom";
import { AdminPaymentsScreen } from "./routes/admin";
import { AppShell } from "./routes/AppShell";
import { BuyDetails } from "./routes/BuyDetails";
import { BuyTicketType } from "./routes/BuyTicketType";
import { Landing } from "./routes/Landing";
import { NotFound } from "./routes/NotFound";
import { Pass } from "./routes/Pass";
import { Payment } from "./routes/Payment";
import { Proof } from "./routes/Proof";
import { RequireRole } from "./routes/RequireRole";
import { Status } from "./routes/Status";
import { Waitlist } from "./routes/Waitlist";

/**
 * Routing and the app shell. Deny by default: every gated route explicitly
 * lists the roles that may see it. `roles` always comes from the session
 * context, which mirrors GET /api/v1/me — never a client-side flag.
 *
 * Flow order is binding (addendum §A2): choose type -> details -> hold ->
 * payment -> proof -> status -> pass.
 */
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Landing />} />

        <Route
          path="buy"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <BuyTicketType />
            </RequireRole>
          }
        />
        <Route
          path="buy/details"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <BuyDetails />
            </RequireRole>
          }
        />
        <Route
          path="buy/pay/:intentId"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <Payment />
            </RequireRole>
          }
        />
        <Route
          path="buy/proof/:intentId"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <Proof />
            </RequireRole>
          }
        />
        <Route
          path="status/:intentId"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <Status />
            </RequireRole>
          }
        />
        <Route
          path="pass"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <Pass />
            </RequireRole>
          }
        />
        <Route path="waitlist" element={<Waitlist />} />

        <Route
          path="admin"
          element={
            <RequireRole allow={["OWNER", "ADMIN"]}>
              <AdminPaymentsScreen />
            </RequireRole>
          }
        />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
