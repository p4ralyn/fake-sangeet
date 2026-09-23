import { Route, Routes } from "react-router-dom";
import { AdminIdsScreen } from "./routes/AdminIds";
import { AppShell } from "./routes/AppShell";
import { Landing } from "./routes/Landing";
import { NotFound } from "./routes/NotFound";
import { Pass } from "./routes/Pass";
import { RequireRole } from "./routes/RequireRole";
import { Scan } from "./routes/Scan";
import { VerifyId } from "./routes/VerifyId";

/**
 * Routing and the app shell. Deny by default: every gated route explicitly
 * lists the roles that may see it. `roles` always comes from the session
 * context, which mirrors GET /api/v1/me — never a client-side flag.
 *
 * **This is a security ticketing app, not a ticket shop.** The event sold out,
 * so the buy flow (choose type -> details -> hold -> payment -> proof ->
 * status -> waitlist) has moved to `feature/booking-flow` rather than being
 * deleted. What is left is the door: hold a pass, verify an ID, review IDs,
 * scan at the gate.
 *
 * /scan sits OUTSIDE the AppShell on purpose. It is an operator tool held at
 * a dark door, not a participant screen: no brand header, no mandala corners,
 * no 34rem column — the camera fills the viewport and the scanner paints its
 * own ground (LLD-v1 §11.5).
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
          path="verify"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <VerifyId />
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
