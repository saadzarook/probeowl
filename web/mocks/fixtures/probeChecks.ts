// Author: @SaadZarook
import type { Run, SaveCheck, SaveCheckReport } from "../../src/api/client";
import { failedValidateRun } from "./runs";
import environmentUnbound from "./backend/save-checks-environment-unbound.json";
import passing from "./backend/save-checks-passing.json";
import timeFilter from "./backend/save-checks-time-filter.json";

// The reports below are the backend's own answers, written and checked by
// backend/tests/test_mock_answers.py, so the mocks can't say anything the backend doesn't (#108).

const passingChecks = (passing as SaveCheckReport).checks;

/** The message a Transaction table read without a time filter or key fails with, as the backend words it. */
export const TIME_FILTER_MESSAGE = (timeFilter as SaveCheckReport).checks.find((c) => c.check === "Time filter" && c.status === "fail")?.message ?? "";

/** One check replaced by a failure, keeping the order. */
function failing(check: string, message: string, location?: string): SaveCheck[] {
  return passingChecks.map((c) => (c.check === check ? { ...c, status: "fail", message, location: location ?? c.location } : c));
}

function report(probe: string | null, checks: SaveCheck[]): SaveCheckReport {
  const count = (status: SaveCheck["status"]) => checks.filter((c) => c.status === status).length;
  return { probe, entity: "topup", checks, passed: count("pass"), warnings: count("warn"), failed: count("fail"), can_save: count("fail") === 0, plans_not_run: 0 };
}

/** topup_completed as it is: every check passes. */
export const passingReport = (probe: string | null): SaveCheckReport => ({ ...(passing as SaveCheckReport), probe });
/** topup_completed on an entity whose record reads topups by status: Expectations and Time filter fail. */
export const timeFilterReport = (probe: string | null): SaveCheckReport => ({ ...(timeFilter as SaveCheckReport), probe });
/** Hand-made on the backend's passing checks: no spec reads this message. */
export const piiReport = (probe: string | null) =>
  report(probe, failing("PII masked", "customer_email is selected by record but not listed in pii, so it would not be masked", "record"));
/**
 * The backend's own answer for a probe enabled in Production while access_db has no binding there:
 * one failure per location, each naming its location and environment. Written and checked by
 * backend/tests/test_mock_answers.py, so the mocks can't say anything the backend doesn't (#108).
 */
export const unboundReport = (probe: string | null): SaveCheckReport => ({ ...(environmentUnbound as SaveCheckReport), probe });
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
