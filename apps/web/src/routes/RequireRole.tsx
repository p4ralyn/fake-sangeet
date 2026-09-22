import type { ReactNode } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { Card } from "../components/Card";
import { useSession } from "../lib/session";
import type { Role } from "../lib/types";

export interface RequireRoleProps {
  /** Roles that may see this route. Deny by default — there is no implicit
   * "allow all" path. Never gate on a client-side boolean; this always
   * checks the roles array from GET /api/v1/me. */
  allow: Role[];
  children: ReactNode;
}

export function RequireRole({ allow, children }: RequireRoleProps) {
  const { user, roles, loading } = useSession();
  const location = useLocation();

  if (loading) {
    return (
      <Card>
        <p>Loading…</p>
      </Card>
    );
  }

  if (!user) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/?next=${encodeURIComponent(next)}`} replace />;
  }

  const permitted = roles.some((role) => allow.includes(role));
  if (!permitted) {
    return (
      <Card>
        <h1>Not available</h1>
        <p>Your account doesn't have access to this page.</p>
        <p>
          <Link to="/">Back to the invitation</Link>
        </p>
      </Card>
    );
  }

  return <>{children}</>;
}
