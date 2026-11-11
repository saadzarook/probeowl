// Author: @SaadZarook
import type { Run, SaveCheck, SaveCheckReport } from "../../src/api/client";
import { failedValidateRun } from "./runs";

/** The message a Transaction table read without a time filter or key fails with. */
export const TIME_FILTER_MESSAGE =
  "topups is a Transaction table: filter on its time column created_at (for example created_at >= :since), or look rows up by a key (a primary key, link or external key column = :parameter)";

/** The checks of wallet's topup_completed, in the order the backend reports them. */
const allPass: SaveCheck[] = ([
  { check: "Expectations", status: "pass", message: "Every expectation refers to a location and field of the entity" },
  { check: "Environments", status: "pass", message: "Every location is bound in each environment" },
  { check: "Identifiers", status: "pass", message: "Every identifier resolves to a location" },
  { check: "Read-only SQL", status: "pass", message: "Every SQL lookup is a single SELECT; keys are bound as parameters", location: "record" },
  { check: "Time filter", status: "pass", message: "topups is read by its key, id = :topup_id", location: "record" },
  { check: "PII masked", status: "pass", message: "customer_email is masked wherever it is selected", location: "record" },
] as Omit<SaveCheck, "plan_not_run">[]).map((c) => ({ ...c, plan_not_run: false }));

/** One check replaced by a failure, keeping the order. */
function failing(check: string, message: string, location?: string): SaveCheck[] {
  return allPass.map((c) => (c.check === check ? { ...c, status: "fail", message, location: location ?? c.location } : c));
}

function report(probe: string | null, checks: SaveCheck[]): SaveCheckReport {
  const count = (status: SaveCheck["status"]) => checks.filter((c) => c.status === status).length;
  return { probe, entity: "topup", checks, passed: count("pass"), warnings: count("warn"), failed: count("fail"), can_save: count("fail") === 0, plans_not_run: 0 };
}

export const passingReport = (probe: string | null) => report(probe, allPass);
export const timeFilterReport = (probe: string | null) => report(probe, failing("Time filter", TIME_FILTER_MESSAGE, "record"));
export const piiReport = (probe: string | null) =>
  report(probe, failing("PII masked", "customer_email is selected by record but not listed in pii, so it would not be masked", "record"));
export const unreadableReport = (reason: string): SaveCheckReport => ({
  probe: null,
  entity: null,
  checks: [{ check: "Probe", status: "fail", message: `the YAML cannot be read: ${reason}`, plan_not_run: false }],
  passed: 0,
  warnings: 0,
  failed: 1,
  can_save: false,
  plans_not_run: 0,
});

/** topup_completed as a draft test run on tp_91f2c7: the same failure, under a draft version. */
export function draftRun(id: string): Run {
  return { ...failedValidateRun(id), probe_version: "draft:sha256:3f9a1c0be2d4" };
}
