// Author: @SaadZarook
import type { ComparedField, HealthyComparison } from "./api/client";

export const RESULT_TITLES: Record<ComparedField["result"], string> = {
  same: "Same",
  different: "Different",
  expected_to_vary: "Expected to vary",
  not_compared: "Not compared",
};

const IN_ORDER: ComparedField["result"][] = ["different", "not_compared", "expected_to_vary"];

/** What the table leads with: differences, then masked fields it could not compare, then what is expected to vary. */
export function partDifferences(rows: ComparedField[]) {
  // A plain field not compared is one a side did not read, which its location's row already says.
  const leads = (row: ComparedField) => row.result === "different" || row.result === "expected_to_vary" || (row.result === "not_compared" && row.masked === true);
  const highlighted = rows.filter(leads).sort((a, b) => IN_ORDER.indexOf(a.result) - IN_ORDER.indexOf(b.result));
  return {
    highlighted,
    hidden: rows.filter((row) => !leads(row)),
    matching: rows.filter((row) => row.result === "same").length,
    notCompared: rows.filter((row) => row.result === "not_compared").length,
  };
}

/** What replaces the table when no row leads it. */
export function nothingHighlighted(matching: number, notCompared: number): string {
  if (matching === 0) return "Nothing could be compared";
  return notCompared === 0 ? "All compared fields match" : "Some fields match; the rest could not be compared";
}

/** A value as the table shows it. A masked field has none: only its result says anything. */
export function shownValue(row: ComparedField, value: ComparedField["failing"]): string {
  if (row.masked) return "masked";
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** The line under the heading: what was compared with, or what starting one will do. */
export function subtitle(found: HealthyComparison, production: boolean): string {
  const attempt = found.attempt;
  if (attempt?.status === "compared" && found.healthy_subject) {
    const how = attempt.pasted ? "the ID you pasted" : attempt.narrow ? "closer match" : "newest passing";
    return `${found.warning ? "Compared with" : "Healthy"} ${found.healthy_subject} · ${how} · run ${attempt.healthy_run ?? ""}`;
  }
  return production
    ? "Runs this probe on a known-good subject, under this run's reason and reference. Up to 3 audited runs."
    : "Runs this probe on a known-good subject. Up to 3 audited runs.";
}
