import { Link, useNavigate } from "react-router-dom";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ZariRule } from "../components/ZariRule";
import { useSession } from "../lib/session";

/**
 * Signed-out landing route. The full invitation hero (availability,
 * countdown, copy from the design spec) belongs to the participant-screens
 * agent — this establishes the shell: sign-in for a visitor, a way onward
 * for someone already signed in.
 */
export function Landing() {
  const { user, loading, signIn } = useSession();
  const navigate = useNavigate();

  return (
    <Card>
      <h1>You're invited</h1>
      <ZariRule />
      {loading ? (
        <p>Loading…</p>
      ) : user ? (
        <>
          <p>
            Signed in as {user.display_name} for {user.event.name}.
          </p>
          <Button variant="primary" onClick={() => navigate("/buy")}>
            Reserve your seat
          </Button>
        </>
      ) : (
        <>
          <p>Sign in with your university account to reserve a seat.</p>
          <Button variant="primary" onClick={() => signIn("/buy")}>
            Sign in
          </Button>
        </>
      )}
      <ZariRule />
      <Link to="/pass">Already have a pass?</Link>
    </Card>
  );
}
