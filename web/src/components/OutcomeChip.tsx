// Author: @SaadZarook
import { withCaveat } from "../runFormat";

export const OUTCOMES = {
  found: { label: "Found", filled: true },
  missing: { label: "Missing", filled: true },
  waiting: { label: "Waiting", filled: true },
  blocked: { label: "Blocked", filled: true },
  ambiguous: { label: "Ambiguous", filled: true },
  stale: { label: "Stale", filled: true },
  error: { label: "Error", filled: false },
  not_configured: { label: "Not configured", filled: false },
} as const;

export type Outcome = keyof typeof OUTCOMES;

/** One chip for the eight outcomes: filled for data, outlined for the system. A caveat is shown in amber, never as a clean outcome. */
export function OutcomeChip({ outcome, caveat }: { outcome: Outcome; caveat?: string | null }) {
  const { label, filled } = OUTCOMES[outcome];
  return (
    <span className="chip" data-outcome={outcome} data-filled={filled} data-caveat={caveat ? true : undefined}>
      {withCaveat(label, caveat)}
    </span>
  );
}
