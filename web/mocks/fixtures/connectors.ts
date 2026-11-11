// Author: @SaadZarook
import type { ConnectorStatus } from "../../src/api/client";

const GUARDS = ["READ COMMITTED", "statement timeout 2 s", "row ceiling 1000", "TLS, server certificate verified"];
const PLAN_MESSAGE =
  "this login cannot read plans, so the plan save checks cannot run: it needs GRANT SHOWPLAN TO <login> in the database";

const ROLES: Record<string, string> = { sqlserver: "Readable secondary", oracle: "Active Data Guard standby", mariadb: "Read replica" };
const FORBIDDEN: Record<string, string[]> = {
  sqlserver: ["NOLOCK", "HOLDLOCK", "TABLOCKX", "UPDLOCK", "temp tables", "OPENROWSET()", "OPENQUERY()", "OPENDATASOURCE()", "OPENXML()", "linked servers"],
  oracle: ["FOR UPDATE", "LOCK TABLE", "PL/SQL blocks", "package calls", "database links"],
  mariadb: ["FOR UPDATE", "LOCK IN SHARE MODE", "GET_LOCK()", "SLEEP()", "BENCHMARK()", "LOAD_FILE()"],
};

const base = (connector: string, type: string, owner: string, environment: string, production: boolean): ConnectorStatus => ({
  connector,
  type,
  owner,
  environment,
  production,
  health: "healthy",
  replica: false,
  role: ROLES[type] ?? null,
  limits: type === "rest" ? ["5 s", "100 req/min", "GET only"] : ["2 s", "100 rows"],
  row_ceiling: 1000,
  guards: GUARDS,
  forbidden: FORBIDDEN[type] ?? [],
});

/** The wallet application's connectors, as the dev stack reports them: SQL readings live, REST never called. */
export function walletConnectors(environment: string, production: boolean): ConnectorStatus[] {
  return [
    {
      ...base("wallet_db", "sqlserver", "payments-team", environment, production),
      replica: true,
      lag_seconds: 2,
      max_replica_lag_seconds: 60,
      plan_access: "readable",
      transport: "TLS, server certificate not verified",
    },
    {
      ...base("ledger_db", "oracle", "payments-team", environment, production),
      replica: true,
      lag_seconds: 4,
      max_replica_lag_seconds: 60,
      plan_access: "readable",
      transport: "not encrypted",
      time_zone: "the database's own (DBTIMEZONE)",
    },
    {
      ...base("checkout_api", "rest", "payments-team", environment, production),
      health: "not_checked",
      reason: "REST bindings are never called, so their health is not checked.",
      plan_access: null,
      transport: "not encrypted",
    },
  ];
}

/** Card access connectors, healthy. */
export function cardAccessConnectors(environment: string, production: boolean): ConnectorStatus[] {
  return [
    { ...base("access_db", "mariadb", "access-team", environment, production), replica: true, lag_seconds: 1, max_replica_lag_seconds: 30, plan_access: "readable", transport: "TLS, server certificate verified" },
    { ...base("door_controller_api", "rest", "access-team", environment, production), health: "not_checked", reason: "REST bindings are never called, so their health is not checked.", plan_access: null, transport: "TLS, server certificate verified" },
  ];
}

/** One response with every health value; the Payouts application is UI-only, so its connector names are not in examples/. */
export function payoutsConnectors(environment: string, production: boolean): ConnectorStatus[] {
  return [
    {
      ...base("refunds_replica", "sqlserver", "payments-team", environment, production),
      health: "not_checked",
      reason: "missing VIEW DATABASE PERFORMANCE STATE",
      replica: true,
      lag_seconds: null,
      lag_note: "replica lag unknown: missing VIEW DATABASE PERFORMANCE STATE",
      max_replica_lag_seconds: 60,
      plan_access: "readable",
      transport: "TLS, server certificate verified",
    },
    { ...base("ledger_replica", "sqlserver", "payments-team", environment, production), replica: true, lag_seconds: 3, max_replica_lag_seconds: 60, plan_access: "readable", transport: "TLS, server certificate verified" },
    {
      ...base("settlement_replica", "oracle", "payments-team", environment, production),
      health: "lagging",
      reason: "the replica is 240 s behind its primary, over the 60 s limit",
      replica: true,
      lag_seconds: 240,
      max_replica_lag_seconds: 60,
      plan_access: "readable",
      transport: "TLS, server certificate verified",
      time_zone: "+04:00 (from the binding)",
    },
    {
      ...base("reporting_replica", "mariadb", "payments-team", environment, production),
      health: "paused",
      reason: "lookups are paused until the replica catches up",
      replica: true,
      lag_seconds: null,
      lag_note: "replication has stopped",
      max_replica_lag_seconds: 60,
      plan_access: "not_checked",
      transport: "TLS, server certificate verified",
    },
    {
      ...base("payout_db", "sqlserver", "payments-team", environment, production),
      health: "unreachable",
      reason: "the connection was refused or could not be made; check the database is up and the binding is right",
      replica: true,
      lag_seconds: null,
      lag_note: "the lag is unknown",
      max_replica_lag_seconds: 60,
      plan_access: "not_checked",
      transport: "not encrypted",
    },
    {
      ...base("bank_files_db", "sqlserver", "payments-team", environment, production),
      replica: true,
      lag_seconds: 5,
      max_replica_lag_seconds: 60,
      plan_access: "denied",
      plan_message: PLAN_MESSAGE,
      transport: "TLS, server certificate verified",
    },
    {
      ...base("bank_api", "rest", "payments-team", environment, production),
      health: "not_checked",
      reason: "REST bindings are never called, so their health is not checked.",
      plan_access: null,
      transport: "TLS, server certificate verified",
    },
    {
      ...base("notify_db", "mariadb", "payments-team", environment, production),
      health: "not_configured",
      reason: "this connector has no binding in this environment",
      role: null,
      limits: [],
      row_ceiling: null,
      guards: [],
      forbidden: [],
      plan_access: "not_checked",
    },
  ];
}
