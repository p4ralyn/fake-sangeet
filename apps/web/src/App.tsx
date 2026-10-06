import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./routes/AppShell";
import { IdReview } from "./routes/ops/IdReview";
import { MailHealth } from "./routes/ops/MailHealth";
import { OpsShell } from "./routes/ops/OpsShell";
import { Tonight } from "./routes/ops/Tonight";
import { Gate } from "./routes/Gate";
import { Landing } from "./routes/Landing";
import { NotFound } from "./routes/NotFound";
import { Tutorial } from "./routes/Tutorial";
import { ParticipantRoute } from "./routes/ParticipantRoute";
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
 * Staff live under /ops, which has its own rail (see OpsShell). Participant
 * screens deliberately do not get that chrome: three screens do not need a
 * rail, and nav furniture around somebody's pass makes the pass worse.
 *
 * RequireRole says who MAY enter; ParticipantRoute says who BELONGS here.
 * Ops staff carry VISITOR/PARTICIPANT like everyone else (PART 3, "Roles"),
 * so the role guard alone would happily show an ADMIN an empty pass screen.
 * The second guard sends them to their dashboard, and sends everyone else
 * through the ID check before the QR.
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
        {/* Public on purpose: the "ticket ready" email links here, and somebody
            who has not signed in yet is exactly who needs it. */}
        <Route path="tutorial" element={<Tutorial />} />

        <Route
          path="verify"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <ParticipantRoute>
                <VerifyId />
              </ParticipantRoute>
            </RequireRole>
          }
        />
        <Route
          path="pass"
          element={
            <RequireRole allow={["VISITOR", "PARTICIPANT"]}>
              <ParticipantRoute requireIdCheck>
                <Pass />
              </ParticipantRoute>
            </RequireRole>
          }
        />

        {/* The gate crew's screen: the room's counts and a way into the
            scanner, with no rail and nothing that names an attendee. It is
            inside the AppShell rather than /ops precisely because it should
            not come with the staff rail. */}
        <Route
          path="gate"
          element={
            <RequireRole allow={["SECURITY", "ADMIN", "OWNER"]}>
              <Gate />
            </RequireRole>
          }
        />

        {/* The staff area. One guard on the parent, so a panel added later
            cannot be reached by forgetting to wrap it. SECURITY is absent on
            purpose — they get /gate and /scan (LLD-v1 §11.5). */}
        <Route
          path="ops"
          element={
            <RequireRole allow={["OWNER", "ADMIN"]}>
              <OpsShell />
            </RequireRole>
          }
        >
          <Route index element={<Tonight />} />
          <Route path="ids" element={<IdReview />} />
          <Route path="mail" element={<MailHealth />} />
        </Route>

        {/* The review queue used to live here and people have it bookmarked. */}
        <Route path="admin/ids" element={<Navigate to="/ops/ids" replace />} />
        <Route path="admin" element={<Navigate to="/ops" replace />} />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
