// Author: @SaadZarook
import type { BindingHealth } from "../api/client";

const HEALTH: Record<BindingHealth, string> = {
  healthy: "Healthy",
  lagging: "Lagging",
  paused: "Paused",
  stopped: "Stopped",
  unreachable: "Unreachable",
  not_checked: "Not checked",
  not_configured: "Not configured",
};

/** One chip per health value; the text is always shown so colour is not the only signal. */
export function HealthChip({ health }: { health: BindingHealth }) {
  return (
    <span className="health-chip" data-health={health}>
      {HEALTH[health]}
    </span>
  );
}
