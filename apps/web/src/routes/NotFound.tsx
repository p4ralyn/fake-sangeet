import { ButtonLink, EmptyState } from "../components";

export function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      action={
        <ButtonLink to="/" variant="secondary">
          Back to the invitation
        </ButtonLink>
      }
    >
      <p>There's nothing here.</p>
    </EmptyState>
  );
}
