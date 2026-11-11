// Author: @SaadZarook
import type { Run } from "../../src/api/client";
import type { components } from "../../src/api/schema";

const base: Omit<Run, "id" | "status" | "locations" | "finished_at"> = {
  application: "wallet",
  environment: "uat",
  probe: "topup_completed",
  probe_title: "Top-up completed",
  kind: "validate",
  probe_version: "3",
  entity: "topup",
  identifier: "topup_id",
  identifier_value: "tp_91f2c7",
  actor: { kind: "person", name: "s.khan" },
  started_at: "2026-01-01T10:00:00Z",
};

const ledgerQuery = `SELECT account, direction, amount
FROM ledger_entries
WHERE txn_id = :ledger_txn_id
FETCH FIRST 100 ROWS ONLY`;

/** topup_completed on tp_91f2c7, partway: the record and payment are found, the rest still to come. */
export function runningValidateRun(id: string): Run {
  return {
    ...base,
    id,
    status: "running",
    finished_at: null,
    locations: [
      { location: "record", title: "Top-up record", outcome: "found" },
      { location: "payment", title: "Provider payment", outcome: "found" },
    ],
  };
}

/** topup_completed on tp_91f2c7, finished: Ledger is Missing, with its evidence. */
export function failedValidateRun(id: string): Run {
  return {
    ...base,
    id,
    status: "failed",
    finished_at: "2026-01-01T10:00:02Z",
    locations: [
      { location: "record", title: "Top-up record", outcome: "found" },
      { location: "payment", title: "Provider payment", outcome: "found" },
      { location: "webhook", title: "Webhook deliveries", outcome: "found" },
      {
        location: "ledger", title: "Ledger entries",
        outcome: "missing",
        reason: "0 rows, expected exactly 2",
        evidence: {
          connector: "ledger_db",
          query: ledgerQuery,
          parameters: { ledger_txn_id: "lt_55e1a0" },
          guards: ["Replica lag 0.4 s", "Timeout 2 s", "Rows ≤ 100"],
          rows: [],
          rows_total: 0,
          started_at: "2026-01-01T10:00:01Z",
          duration_ms: 61,
        },
      },
    ],
    expectations: [
      { step: 0, expectation: "record.status in ['succeeded', 'failed', 'pending']", result: "pass", detail: "succeeded" },
      { step: 1, expectation: "payment.amount == record.amount", result: "pass", detail: "500.00 = 500.00" },
      { step: 2, expectation: "count(webhook) >= 1", result: "pass", detail: "2" },
      { step: 3, expectation: "count(ledger) == 2", result: "fail", detail: "got 0" },
      { step: 3, expectation: "sum(ledger.amount where direction == 'credit') == sum(ledger.amount where direction == 'debit')", result: "skip", detail: "no ledger rows to compare" },
    ],
  };
}

/** topup_completed on tp_lagunk in Production: ledger_db's lag is unknown, so the Missing ledger may only be late. */
export function lateValidateRun(id: string): Run {
  const failed = failedValidateRun(id);
  const caveat = "replica lag unknown, the record may not have arrived yet";
  return {
    ...failed,
    environment: "prod",
    identifier_value: "tp_lagunk",
    investigation: { reason: "incident", reference: "WALLET-482" },
    warnings: ["Replica lag unknown for ledger_db (missing REPLICA MONITOR): Missing results can't be confirmed fresh"],
    caveat: "replica lag unknown: 1 Missing result may not have arrived yet",
    locations: (failed.locations ?? []).map((l) => (l.location === "ledger" ? { ...l, caveat } : l)),
    expectations: (failed.expectations ?? []).map((e) =>
      e.expectation === "count(ledger) == 2" ? { ...e, caveat: "reads a Missing record that may not have arrived yet: replica lag unknown" } : e,
    ),
  };
}

/** topup_completed on tp_halted: a kill switch stopped it after its first location. */
export function stoppedValidateRun(id: string): Run {
  const failed = failedValidateRun(id);
  return {
    ...failed,
    expectations: [],
    locations: (failed.locations ?? []).slice(0, 1),
    stop_reason: "stopped by the kill switch on connector wallet_db: replica on fire",
  };
}

/** topup_completed on tp_paused: the circuit breaker for ledger_db is open, so the run is skipped. */
export function skippedValidateRun(id: string): Run {
  return {
    ...base,
    id,
    status: "skipped",
    skip_reason: "ledger_db is paused: replica lag 120 s (max 60 s); paused for another 42 s",
    finished_at: "2026-01-01T10:00:01Z",
    locations: [],
  };
}

type RunSummary = components["schemas"]["RunSummary"];

const summary = (id: string, minute: number, changes: Partial<RunSummary> = {}): RunSummary => ({
  id,
  application: "wallet",
  environment: "uat",
  probe: "topup_completed",
  probe_title: "Top-up completed",
  kind: "validate",
  draft: false,
  identifier: "topup_id",
  identifier_value: "tp_91f2c7",
  subject: "tp_91f2c7",
  status: "failed",
  actor: "s.khan",
  reason: "incident",
  reference: "WALLET-482",
  started_at: `2026-11-08T10:${String(minute).padStart(2, "0")}:00Z`,
  finished_at: `2026-11-08T10:${String(minute).padStart(2, "0")}:02Z`,
  ...changes,
});

/** Wallet's runs in UAT, newest first, in two pages: each kind of row the Runs list shows. */
export const walletRunPages: RunSummary[][] = [
  [
    summary("run_demo", 50),
    summary("run_draft", 45, { draft: true, status: "passed", subject: "tp_91f2b9", identifier_value: "tp_91f2b9", reason: "proactive_check", reference: null }),
    summary("run_stopped", 40, { stop_reason: "stopped by the global kill switch: replica on fire" }),
  ],
  [
    summary("run_late", 30, { caveat: "replica lag unknown: 1 Missing result may not have arrived yet" }),
    summary("run_email", 20, { identifier: "provider_payment_id", identifier_value: "[masked]", subject: null, status: "ambiguous", finished_at: "2026-11-08T10:20:01Z" }),
  ],
];
