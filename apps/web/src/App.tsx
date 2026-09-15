import { Route, Routes } from "react-router-dom";
import { AdminPaymentsScreen } from "./routes/admin";
import { AppShell } from "./routes/AppShell";
import { Landing } from "./routes/Landing";
import { NotFound } from "./routes/NotFound";
import { BuyDetailsScreen, BuyTicketTypeScreen, PassScreen, PaymentScreen } from "./routes/participant";
import { RequireRole } from "./routes/RequireRole";

/**
 * Routing and the app shell. Deny by default: every gated route explicitly
 * lists the roles that may see it. `roles` always comes from the session
 * context, which mirrors GET /api/v1/me — never a client-side flag.
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
              <BuyTicketTypeScreen />
            </RequireRole>
          }
        />
        <Route
          path="buy/details"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <BuyDetailsScreen />
            </RequireRole>
          }
        />
        <Route
          path="pay/:intentId"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <PaymentScreen />
            </RequireRole>
          }
        />
        <Route
          path="pass"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <PassScreen />
            </RequireRole>
          }
        />

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
