import { Route, Routes } from "react-router-dom";
import { AdminIdsScreen } from "./routes/AdminIds";
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
import { Scan } from "./routes/Scan";
import { Status } from "./routes/Status";
import { VerifyId } from "./routes/VerifyId";
import { Waitlist } from "./routes/Waitlist";

/**
 * Routing and the app shell. Deny by default: every gated route explicitly
 * lists the roles that may see it. `roles` always comes from the session
 * context, which mirrors GET /api/v1/me — never a client-side flag.
 *
 * Flow order is binding (addendum §A2): choose type -> details -> hold ->
 * payment -> proof -> status -> pass.
 *
 * /scan sits OUTSIDE the AppShell on purpose. It is an operator tool held at
 * a dark door, not a participant screen: no brand header, no mandala
 * corners, no 34rem column — the camera fills the viewport and the scanner
 * paints its own ground (LLD-v1 §11.5).
 */
export function App() {
  return (
    <Routes>
      <Route
        path="scan"
        element={
          <RequireRole allow={["SECURITY", "ADMIN", "OWNER"]}>
            <Scan />
          </RequireRole>
        }
      />

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
        <Route
          path="verify"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <VerifyId />
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
        <Route
          path="admin/ids"
          element={
            <RequireRole allow={["OWNER", "ADMIN"]}>
              <AdminIdsScreen />
            </RequireRole>
          }
        />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
