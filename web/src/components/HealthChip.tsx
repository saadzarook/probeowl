// Author: @SaadZarook
import type { BindingHealth } from "../api/client";
import { Badge, type BadgeTone } from "../ui";

const HEALTH: Record<BindingHealth, { label: string; tone: BadgeTone }> = {
  healthy: { label: "Healthy", tone: "found" },
  lagging: { label: "Lagging", tone: "waiting" },
  paused: { label: "Paused", tone: "stale" },
  stopped: { label: "Stopped", tone: "production" },
  unreachable: { label: "Unreachable", tone: "error" },
  not_checked: { label: "Not checked", tone: "blocked" },
  not_configured: { label: "Not configured", tone: "not-configured" },
};

/** One chip per health value; the text is always shown so colour is not the only signal. */
export function HealthChip({ health }: { health: BindingHealth }) {
  const { label, tone } = HEALTH[health];
  return (
    <Badge tone={tone} data-health={health}>
      {label}
    </Badge>
  );
}
