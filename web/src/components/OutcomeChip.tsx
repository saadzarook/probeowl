// Author: @SaadZarook
import { withCaveat } from "../runFormat";
import { Badge, type BadgeTone } from "../ui";

// Filled chips describe data; outlined ones, Error and Not configured, describe the system.
export const OUTCOMES = {
  found: { label: "Found", tone: "found" },
  missing: { label: "Missing", tone: "missing" },
  waiting: { label: "Waiting", tone: "waiting" },
  blocked: { label: "Blocked", tone: "blocked" },
  ambiguous: { label: "Ambiguous", tone: "ambiguous" },
  stale: { label: "Stale", tone: "stale" },
  error: { label: "Error", tone: "error" },
  not_configured: { label: "Not configured", tone: "not-configured" },
} as const satisfies Record<string, { label: string; tone: BadgeTone }>;

export type Outcome = keyof typeof OUTCOMES;

/** One chip for the eight outcomes: filled for data, outlined for the system. A caveat is shown in amber, never as a clean outcome. */
export function OutcomeChip({ outcome, caveat }: { outcome: Outcome; caveat?: string | null }) {
  const { label, tone } = OUTCOMES[outcome];
  return (
    <Badge tone={caveat ? "waiting" : tone} data-outcome={outcome} data-caveat={caveat ? true : undefined}>
      {withCaveat(label, caveat)}
    </Badge>
  );
}
