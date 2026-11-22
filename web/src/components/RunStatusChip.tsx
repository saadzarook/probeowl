// Author: @SaadZarook
import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "../ui";

// A run's status in the outcome colours. Not checked and running are the waiting colours: not a
// pass and not a finding (#81).
const TONE: Record<string, BadgeTone> = { passed: "found", failed: "missing", running: "waiting", not_checked: "waiting" };

/** A run's status as a chip, in amber whenever the run carries a caveat (rule 9). */
export function RunStatusChip({ status, caveat, children }: { status: string; caveat: boolean; children: ReactNode }) {
  return (
    <Badge tone={caveat ? "waiting" : (TONE[status] ?? "blocked")} data-status={status} data-caveat={caveat || undefined}>
      {children}
    </Badge>
  );
}
