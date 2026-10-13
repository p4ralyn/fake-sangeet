import { DisplayTitle, Stat, StatGrid } from "../../components";
import type { OpsSummary, TagCount } from "../../lib/types";
import styles from "./Panels.module.css";

/**
 * The two boards that answer "how full is the room" and "is the door working".
 *
 * They live apart from Tonight because two screens now draw them: the ops
 * dashboard, which follows them with the queue board, and the gate screen a
 * SECURITY volunteer gets, which must not show queues at all. Copying them
 * would mean the number a volunteer reads and the number an owner reads could
 * drift apart on the one night that matters.
 *
 * The stat tiles themselves are the design system's `Stat` stubs.
 */

export function InTheRoom({ data, heading }: { data: OpsSummary; heading: string }) {
  const { occupancy, seats } = data;
  const pct = seats.total ? Math.round((100 * occupancy.inside) / seats.total) : 0;

  return (
    <section className={styles.section} aria-label="In the room">
      <DisplayTitle tone="ink" className={styles.title}>
        {heading}
      </DisplayTitle>
      <StatGrid>
        <Stat
          feature
          value={occupancy.inside}
          label="In the room"
          hint={`${pct}% of ${seats.total}`}
          tone={pct >= 90 ? "warn" : "good"}
        />
        <Stat value={occupancy.on_break} label="On a break" />
        <Stat value={occupancy.not_arrived} label="Not arrived" />
        <Stat value={occupancy.departed} label="Left" />
      </StatGrid>
      {data.by_tag.length > 0 && (
        // Baarati and gharwale (14 Oct 2026). Counts only, so it is safe on
        // the gate screen too; both sides always drawn, even at zero.
        <StatGrid>
          {data.by_tag.map((side) => (
            <Stat
              key={side.tag}
              value={side.inside}
              label={`${SIDE_LABEL[side.tag]} inside`}
              hint={`of ${side.tickets} · ${side.on_break} on a break`}
            />
          ))}
        </StatGrid>
      )}
    </section>
  );
}

const SIDE_LABEL: Record<TagCount["tag"], string> = {
  BAARATI: "Baarati",
  GHARWALE: "Gharwale",
};

/** Is the gate alive, and is it turning people away. */
export function TheDoor({ data }: { data: OpsSummary }) {
  const { seats } = data;

  return (
    <section className={styles.section} aria-label="The door">
      <DisplayTitle as="h2" size="section" tone="ink" className={styles.subtitle}>
        The door
      </DisplayTitle>
      <StatGrid>
        <Stat
          value={data.scans_recent}
          label={`Scans, last ${data.scans_window_minutes} min`}
          // A gate that has gone quiet at 18:20 is either finished or broken,
          // and the difference is worth knowing before the queue tells you.
          hint={data.scans_recent === 0 ? "Quiet — check the gate is up" : undefined}
          tone={data.scans_recent === 0 ? "warn" : undefined}
        />
        <Stat
          value={data.refusals_recent}
          label="Refused"
          tone={data.refusals_recent > 0 ? "bad" : undefined}
        />
        <Stat
          value={seats.committed}
          label="Seats sold"
          hint={`${seats.total - seats.committed} free of ${seats.total}`}
        />
      </StatGrid>
    </section>
  );
}
