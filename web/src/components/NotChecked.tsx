// Author: @SaadZarook
import type { LocationResult, Run } from "../api/client";

// Locations whose data a Not checked run could not use (#81). Blocked is listed with its reason,
// which names the location its chain starts at.
const UNREAD: ReadonlySet<LocationResult["outcome"]> = new Set(["error", "stale", "not_configured", "waiting", "blocked"]);

/** A Not checked run's summary: nothing failed, and which locations could not be read, with why. */
export function NotChecked({ run }: { run: Run }) {
  if (run.status !== "not_checked") return null;
  const unread = (run.locations ?? []).filter((l) => UNREAD.has(l.outcome));
  if (unread.length === 0) return null; // stopped before reading: StopReason says why
  return (
    <div className="summary not-checked" role="status">
      <p>Not checked: nothing failed, but data some expectations need could not be read.</p>
      <ul>
        {unread.map((l) => (
          <li key={`${l.location}:${l.row ?? ""}`}>
            {l.title || l.location}: {l.outcome.replaceAll("_", " ")}
            {l.reason ? `, ${l.reason}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
