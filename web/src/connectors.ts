// Author: @SaadZarook
import type { ConnectorStatus } from "./api/client";

const TYPES: Record<string, string> = { sqlserver: "SQL Server", oracle: "Oracle", mariadb: "MariaDB", rest: "REST" };

/** The connector type as a person says it; an unknown type is shown as given. */
export function typeTitle(type: string): string {
  return TYPES[type] ?? type;
}

/** Replica lag as shown in the row: seconds, the note when the lag is unknown, or n/a for a non-replica. */
export function lagText(status: ConnectorStatus): string {
  if (status.lag_seconds != null) return `${status.lag_seconds} s`;
  if (status.lag_note) return status.lag_note;
  return status.replica ? "Unknown" : "n/a";
}

/** The limits as shown in the row: the time limit, then the row cap or the rate budget. */
export function limitsText(status: ConnectorStatus): string {
  const limits = status.limits ?? [];
  return limits.length === 0 ? "—" : limits.join(" · ");
}

/** Whether Test connection can do anything: a stopped connector is refused, one with no binding has nothing to reach. */
export function canTestConnection(status: ConnectorStatus): boolean {
  return status.health !== "stopped" && status.health !== "not_configured";
}

/** A binding someone should look at: runs on it are paused, behind or failing. */
export function needsAttention(status: ConnectorStatus): boolean {
  return status.health === "lagging" || status.health === "paused" || status.health === "stopped" || status.health === "unreachable";
}

/** Plan access in words; a REST connector has no plans. */
export function planText(status: ConnectorStatus): string {
  if (status.plan_access == null) return "Not applicable, a REST API has no plans.";
  const word = { readable: "Readable", denied: "Denied", not_checked: "Not checked" }[status.plan_access];
  return status.plan_message ? `${word}. ${status.plan_message}` : word;
}

/** The warning to show in the row: plans unreadable or unchecked in Production, where a save without plan checks needs acknowledging. */
export function planWarning(status: ConnectorStatus): string | undefined {
  if (!status.production) return undefined;
  if (status.plan_access !== "denied" && status.plan_access !== "not_checked") return undefined;
  return status.plan_message ?? undefined;
}
