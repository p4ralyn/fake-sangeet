import { Card } from "../components/Card";

export interface PlaceholderProps {
  title: string;
  note?: string;
}

/**
 * A named component rendering the screen title inside a Card. Stands in for
 * a screen built by another agent — do not add flow logic here.
 */
export function Placeholder({ title, note }: PlaceholderProps) {
  return (
    <Card>
      <h1>{title}</h1>
      {note && <p>{note}</p>}
    </Card>
  );
}
