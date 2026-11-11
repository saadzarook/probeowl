// Author: @SaadZarook
import type { ConfigReload } from "./api/client";

function counted(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** What a reload loaded and changed, as sentences for the Connectors screen. */
export function reloadSummary(report: ConfigReload): string[] {
  const loaded = `Reloaded: ${counted(report.applications, "application")}, ${counted(report.connectors, "connector")}, ${counted(report.probes, "probe")}.`;
  const changes: [string, string[] | undefined][] = [
    ["Applications added", report.applications_added],
    ["Applications removed", report.applications_removed],
    ["Connectors added", report.connectors_added],
    ["Connectors removed", report.connectors_removed],
    ["Connectors changed", report.connectors_changed],
  ];
  const changed = changes.filter(([, names]) => names !== undefined && names.length > 0).map(([label, names]) => `${label}: ${(names ?? []).join(", ")}.`);
  return [loaded, ...(changed.length > 0 ? changed : ["No application or connector changed."]), report.note];
}
