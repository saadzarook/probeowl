// Author: @SaadZarook
import type { Entity, ExpectationEvaluation, LocationResult, Run } from "./api/client";

export const RESULT_LABELS: Record<ExpectationEvaluation["result"], string> = { pass: "Pass", fail: "Fail", skip: "Skip" };

/** "ledger_entries" -> "Ledger entries". */
export function titleCase(name: string): string {
  const spaced = name.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** "Missing - replica lag unknown, the record may not have arrived yet": a label, and why it may not be final. */
export function withCaveat(label: string, caveat: string | null | undefined): string {
  return caveat ? `${label} - ${caveat}` : label;
}

/** An ISO timestamp as "2026-01-01 10:00:00 UTC". */
export function formatTime(iso: string): string {
  return `${iso.replace("T", " ").slice(0, 19)} UTC`;
}

// What owl serve shows in place of a PII value; such a value is never put in a command.
const MASKED = "[masked]";

/** One word of a shell command: as it is when safe, else single-quoted. */
function shellWord(text: string): string {
  if (/^[\w.@:+/-]+$/.test(text)) return text;
  return `'${text.replaceAll("'", String.raw`'\''`)}'`;
}

/** "owl validate topup tp_91f2c7 --env uat ...": the command that runs this probe on this subject again. */
export function cliCommand(run: Run): string {
  const value = run.identifier_value === MASKED ? `<${run.identifier}>` : shellWord(run.identifier_value);
  const where = `--env ${run.environment} --application ${run.application} --probe ${run.probe}`;
  const record = run.investigation;
  let why = "";
  if (record) why += ` --reason ${record.reason}`;
  if (record?.reference) why += ` --reference ${shellWord(record.reference)}`;
  if (run.kind === "validate") return `owl validate ${run.entity} ${value} ${where}${why}`;
  return `owl inspect ${value} ${where} --entity ${run.entity} --identifier ${run.identifier}${why}`;
}

function shownParameter(value: unknown): string {
  if (value === null) return "null";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/**
 * "ledger_txn_id = lt_55e1a0 · from record": the key a location was looked up by, as its evidence
 * bound it (masked values stay masked), and where the key came from. Undefined when no lookup ran.
 */
export function keyLine(location: LocationResult, entity: Entity | undefined): string | undefined {
  const parameters = Object.entries(location.evidence?.parameters ?? {});
  if (parameters.length === 0) return undefined;
  const keys = parameters.map(([name, value]) => `${name} = ${shownParameter(value)}`).join(", ");
  let from: string | undefined;
  if (location.parent != null) from = `from ${location.parent} row ${(location.row ?? 0) + 1}`;
  else {
    const keyFrom = entity?.locations[location.location]?.key_from;
    if (keyFrom) from = `from ${keyFrom.split(".")[0]}`;
  }
  return from ? `${keys} · ${from}` : keys;
}
