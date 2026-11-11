// Author: @SaadZarook
import type { RunbookView, StepInRun } from "../../src/api/client";

const OUTBOX_SQL = `SELECT TOP (20) id, status, attempts, last_error, created_at
FROM outbox_events WHERE aggregate_id = :topup_id
ORDER BY created_at DESC
`;
const LOCKS_SQL = `SELECT sid, blocking_session, event, seconds_in_wait
FROM v$session WHERE blocking_session IS NOT NULL
`;
const REQUEUE_SQL = `UPDATE outbox_events SET status = 'pending', attempts = 0
WHERE aggregate_id = :topup_id AND status = 'failed'
`;
const ERRORS_QUERY = `fetch logs
| filter loglevel == "ERROR" and contains(content, :topup_id)
| limit 100
`;

/** examples/wallet/runbooks/topup_not_credited.yaml, as the API labels it. */
export const topUpNotCredited: RunbookView = {
  runbook: "topup_not_credited",
  title: "Top-up captured but wallet not credited",
  application: "wallet",
  applies_to: { entity: "topup", when: ["ledger.missing"] },
  owner: "payments-team",
  last_verified: "2026-10-12",
  review_due: false,
  tables: ["ledger_db.v$session", "wallet_db.outbox_events"],
  drifted_tables: [],
  steps: [
    {
      kind: "read_query",
      id: "check-outbox",
      title: "Is the ledger posting stuck in the outbox?",
      using: "wallet_db",
      sql: OUTBOX_SQL,
      reviewed: { by: "s.khan", date: "2026-10-12", sql_sha256: "a".repeat(64) },
      runnable: true,
      why_copy_only: null,
    },
    {
      kind: "read_query",
      id: "check-locks",
      title: "Is the ledger posting job blocked by a lock?",
      using: "ledger_db",
      sql: LOCKS_SQL,
      reviewed: null,
      runnable: false,
      why_copy_only: "not reviewed yet",
    },
    {
      kind: "external_query",
      id: "ledger-errors",
      title: "Ledger service errors for this top-up",
      tool: "dynatrace",
      query: ERRORS_QUERY,
      runnable: false,
    },
    {
      kind: "manual",
      id: "confirm-capture",
      title: "Confirm the capture in the provider dashboard",
      text: "Search the payment ID; check for a refund, dispute or partial capture.",
      runnable: false,
    },
    {
      kind: "write_query",
      id: "requeue-posting",
      title: "Re-queue the ledger posting",
      using: "wallet_db",
      requires: "change approval; run by a DBA",
      sql: REQUEUE_SQL,
      relabelled: false,
      runnable: false,
    },
    { kind: "probe", id: "revalidate", title: "Re-validate after the fix", probe: "topup_completed", runnable: true },
  ],
};

/** A runbook not in examples/: its review is due, a table drifted, and a read was relabelled. */
export const flaggedRunbook: RunbookView = {
  runbook: "duplicate_webhooks",
  title: "Duplicate webhook deliveries",
  application: "wallet",
  applies_to: { entity: "topup", when: ["webhook.ambiguous"] },
  owner: "finance-eng",
  last_verified: "2026-01-05",
  review_due: true,
  tables: ["wallet_db.provider_webhooks"],
  drifted_tables: ["wallet_db.provider_webhooks"],
  steps: [
    {
      kind: "write_query",
      id: "clear-duplicates",
      title: "Clear the duplicates",
      using: "wallet_db",
      requires: "labelled a read query, but it is not a single SELECT: review it as a write query",
      sql: "DELETE FROM provider_webhooks WHERE id = :id\n",
      relabelled: true,
      runnable: false,
    },
  ],
};

const fillIn = (text: string, value: string, quote: string) => text.replaceAll(":topup_id", `${quote}${value}${quote}`);

/** The example's steps in a run on value. */
export function stepsInRun(value: string, done: Set<string>, results: Map<string, StepInRun["result"]>): StepInRun[] {
  const state = (id: string, filled: string | null, unfilled: StepInRun["unfilled"] = []): StepInRun => ({
    id,
    filled,
    unfilled,
    done: done.has(id),
    result: results.get(id) ?? null,
  });
  return [
    state("check-outbox", fillIn(OUTBOX_SQL, value, "'")),
    state("check-locks", LOCKS_SQL),
    state("ledger-errors", fillIn(ERRORS_QUERY, value, '"')),
    state("confirm-capture", null),
    state("requeue-posting", fillIn(REQUEUE_SQL, value, "'")),
    state("revalidate", null),
  ];
}

/** What the reviewed read finds in the outbox. */
export function outboxResult(warnings: string[] = []): NonNullable<StepInRun["result"]> {
  return {
    outcome: "found",
    reason: null,
    warnings,
    actor: "s.khan",
    at: "2026-11-07T10:00:00Z",
    evidence: {
      connector: "wallet_db",
      query: OUTBOX_SQL,
      parameters: { topup_id: "tp_91f2c7" },
      guards: ["SET LOCK_TIMEOUT 0"],
      rows: [{ id: 7, status: "failed", attempts: 3 }],
      rows_total: null,
      column_types: { id: "integer", status: "text", attempts: "integer" },
      started_at: "2026-11-07T10:00:00Z",
      duration_ms: 12,
    },
  };
}
