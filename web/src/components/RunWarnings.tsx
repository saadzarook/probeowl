// Author: @SaadZarook
import type { Run } from "../api/client";

/** What limits how far a run can be trusted, one line each as given; a notice, not a failure. */
export function RunWarnings({ run }: { run: Run }) {
  const warnings = run.warnings ?? [];
  if (warnings.length === 0) return null;
  return (
    <ul className="warnings" aria-label="Warnings">
      {warnings.map((warning) => (
        <li key={warning}>{warning}</li>
      ))}
    </ul>
  );
}
