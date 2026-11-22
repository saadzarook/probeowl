// Author: @SaadZarook
import type { Run } from "../api/client";

/** "1 check not applicable in UAT: door_controller_api has no binding here", per connector. */
export function notApplicableLines(run: Run, environment: string): string[] {
  return (run.not_applicable ?? []).map(
    (item) => `${item.checks} ${item.checks === 1 ? "check" : "checks"} not applicable in ${environment}: ${item.connector} has no binding here`,
  );
}

/**
 * What a connector with no binding in this environment left unchecked (#25). The run can still be
 * Passed: Not configured is a known gap here, not a fault. Read from the run's fields, never a message.
 */
export function NotApplicableNote({ run, environment }: { run: Run; environment: string }) {
  const lines = notApplicableLines(run, environment);
  if (lines.length === 0) return null;
  return (
    <div role="note" aria-label="Not applicable" className="grid gap-1 rounded-lg border border-border bg-background p-3 text-sm text-foreground">
      {lines.map((line) => (
        <p key={line} className="m-0">
          {line}
        </p>
      ))}
      <p className="m-0 text-muted">Bind the connector in this environment to check them. owl validate and owl inspect exit 2 for this with --strict.</p>
    </div>
  );
}
