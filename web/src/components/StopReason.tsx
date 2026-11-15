// Author: @SaadZarook
import type { Run } from "../api/client";

/**
 * Next to the status: why a run ended early. Not checked: it stopped before checking, so it says
 * nothing about the flow. Failed: what it found, such as no subject with this value.
 */
export function StopReason({ run }: { run: Run }) {
  if (!run.stop_reason) return null;
  if (run.status === "not_checked") {
    return (
      <span className="stop-reason" role="status">
        Stopped before checking, not a finding: {run.stop_reason}
      </span>
    );
  }
  if (run.status !== "failed") return null;
  return (
    <span className="stop-reason" role="status">
      {run.stop_reason}
    </span>
  );
}
