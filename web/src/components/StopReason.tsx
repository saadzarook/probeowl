// Author: @SaadZarook
import type { Run } from "../api/client";

/** Next to the status: why a run ended early. It stopped, so it says nothing about the flow itself. */
export function StopReason({ run }: { run: Run }) {
  if (run.status !== "failed" || !run.stop_reason) return null;
  return (
    <span className="stop-reason" role="status">
      Stopped, not a finding: {run.stop_reason}
    </span>
  );
}
