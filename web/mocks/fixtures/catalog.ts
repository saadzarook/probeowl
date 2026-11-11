// Author: @SaadZarook
import type { Catalog, SchemaSnapshot, TableSchema } from "../../src/api/client";

/** Mirrors examples/wallet/catalog.yaml; `npm run check:mocks` compares them. */
export const walletCatalog: Catalog = {
  catalog: "wallet",
  tables: [
    {
      connector: "wallet_db",
      table: "topups",
      label: "transaction",
      owner: "payments-team",
      description: "One row per top-up attempt.",
      columns: {
        id: { roles: ["primary_key"] },
        created_at: { roles: ["time"], indexed: true },
        status: { roles: ["status", "compare"], values: ["pending", "succeeded", "failed"] },
        provider_payment_id: { roles: ["external_key"], links_to: { connector: "checkout_api" } },
        ledger_txn_id: { roles: ["link"], links_to: { connector: "ledger_db", table: "ledger_entries", column: "txn_id" } },
        amount: { roles: ["compare", "expected_to_vary"] },
        payment_method: { roles: ["compare"] },
        app_version: { roles: ["compare"] },
        customer_email: { roles: ["pii"] },
      },
    },
    {
      connector: "wallet_db",
      table: "provider_webhooks",
      label: "log",
      owner: "payments-team",
      description: "Webhook deliveries from the payment provider, one row per delivery.",
      retention: "30d",
      columns: {
        id: { roles: ["primary_key"] },
        received_at: { roles: ["time"], indexed: true },
        payment_id: { roles: ["external_key"], links_to: { connector: "checkout_api" } },
      },
    },
    {
      connector: "wallet_db",
      table: "outbox_events",
      label: "outbox",
      owner: "payments-team",
      description: "Events waiting to be published, including ledger postings.",
      columns: {
        id: { roles: ["primary_key"] },
        created_at: { roles: ["time"], indexed: true },
        status: { roles: ["status"], values: ["pending", "published", "failed"] },
        aggregate_id: { roles: ["link"], links_to: { connector: "wallet_db", table: "topups", column: "id" } },
      },
    },
    {
      connector: "ledger_db",
      table: "ledger_entries",
      label: "transaction",
      owner: "ledger-team",
      description: "Double-entry postings; a completed top-up has one credit and one debit.",
      columns: {
        txn_id: { roles: ["primary_key"] },
        direction: { roles: ["primary_key", "compare"] },
        posted_at: { roles: ["time"], indexed: true },
        amount: { roles: ["compare", "expected_to_vary"] },
      },
    },
  ],
};

/** Mirrors examples/card-access/catalog.yaml. */
export const cardAccessCatalog: Catalog = {
  catalog: "card_access",
  tables: [
    {
      connector: "access_db",
      table: "visitor_profile",
      label: "master",
      owner: "access-team",
      description: "One row per visitor, employee or contractor.",
      columns: {
        id: { roles: ["primary_key"] },
        status: { roles: ["status", "compare"], values: ["active", "inactive"] },
        type: { roles: ["compare"] },
        email: { roles: ["pii"] },
      },
    },
    {
      connector: "access_db",
      table: "cards",
      label: "master",
      owner: "access-team",
      description: "Access cards and their validity; the controller holds its own copy of status.",
      columns: {
        card_id: { roles: ["primary_key"] },
        visitor_profile_id: { roles: ["link"], links_to: { connector: "access_db", table: "visitor_profile", column: "id" } },
        status: { roles: ["status", "compare"], values: ["active", "revoked"] },
        valid_to: { roles: ["compare", "expected_to_vary"] },
        access_group: { roles: ["compare"] },
      },
    },
  ],
};

const col = (name: string, type: string, nullable = false) => ({ name, type, nullable });
const idx = (name: string, columns: string[], unique = false) => ({ name, columns, unique });

const walletTables: TableSchema[] = [
  {
    schema: "dbo",
    table: "topups",
    columns: [
      col("id", "varchar(32)"),
      col("user_id", "varchar(32)"),
      col("provider_payment_id", "varchar(64)", true),
      col("ledger_txn_id", "varchar(32)", true),
      col("status", "varchar(16)"),
      col("amount", "decimal(18,2)"),
      col("currency", "char(3)"),
      col("payment_method", "varchar(16)"),
      col("app_version", "varchar(16)", true),
      col("created_at", "datetime2"),
      col("customer_email", "nvarchar(256)", true),
    ],
    primary_key: ["id"],
    indexes: [idx("pk_topups", ["id"], true), idx("ix_topups_created_at", ["created_at"])],
    approx_rows: 48_000_000,
  },
  {
    schema: "dbo",
    table: "provider_webhooks",
    columns: [col("id", "varchar(32)"), col("payment_id", "varchar(64)"), col("event_type", "varchar(32)"), col("received_at", "datetime2")],
    primary_key: ["id"],
    indexes: [idx("pk_provider_webhooks", ["id"], true), idx("ix_webhooks_received_at", ["received_at"])],
    approx_rows: 120_000_000,
  },
  {
    schema: "dbo",
    table: "outbox_events",
    columns: [col("id", "varchar(32)"), col("aggregate_id", "varchar(32)"), col("status", "varchar(16)"), col("created_at", "datetime2")],
    primary_key: ["id"],
    indexes: [idx("pk_outbox_events", ["id"], true)],
    approx_rows: 9_000_000,
  },
  {
    schema: "dbo",
    table: "wallets",
    columns: [col("id", "varchar(32)"), col("user_id", "varchar(32)"), col("balance", "decimal(18,2)"), col("currency", "char(3)")],
    primary_key: ["id"],
    indexes: [idx("pk_wallets", ["id"], true)],
    approx_rows: 6_000_000,
  },
  {
    schema: "dbo",
    table: "currencies",
    columns: [col("code", "char(3)"), col("name", "varchar(64)")],
    primary_key: ["code"],
    indexes: [idx("pk_currencies", ["code"], true)],
    approx_rows: 42,
  },
  {
    schema: "dbo",
    table: "topup_status_history",
    columns: [col("id", "varchar(32)"), col("topup_id", "varchar(32)"), col("status", "varchar(16)"), col("changed_at", "datetime2")],
    primary_key: ["id"],
    indexes: [idx("pk_topup_status_history", ["id"], true)],
    approx_rows: 150_000_000,
  },
  {
    schema: "dbo",
    table: "user_provider_ids",
    columns: [col("user_id", "varchar(32)"), col("provider_user_id", "varchar(64)")],
    primary_key: ["user_id"],
    indexes: [idx("pk_user_provider_ids", ["user_id"], true)],
    approx_rows: 5_000_000,
  },
];

// Oracle names are lower-case here, as the backend returns them.
const ledgerTables: TableSchema[] = [
  {
    schema: "LEDGER",
    table: "ledger_entries",
    columns: [
      col("txn_id", "varchar2(32)"),
      col("direction", "varchar2(6)"),
      col("account", "varchar2(32)"),
      col("amount", "number(18,2)"),
      col("posted_at", "timestamp(6)"),
    ],
    primary_key: ["txn_id", "direction"],
    indexes: [idx("pk_ledger_entries", ["txn_id", "direction"], true), idx("ix_ledger_posted_at", ["posted_at"])],
    approx_rows: 400_000_000,
  },
];

const accessTables: TableSchema[] = [
  {
    schema: null,
    table: "visitor_profile",
    columns: [col("id", "varchar(16)"), col("status", "varchar(16)"), col("type", "varchar(16)"), col("email", "varchar(256)", true)],
    primary_key: ["id"],
    indexes: [idx("PRIMARY", ["id"], true)],
    approx_rows: 2_100_000,
  },
  {
    schema: null,
    table: "cards",
    columns: [
      col("card_id", "varchar(16)"),
      col("visitor_profile_id", "varchar(16)"),
      col("status", "varchar(16)"),
      col("valid_to", "date", true),
      col("access_group", "varchar(32)", true),
    ],
    primary_key: ["card_id"],
    indexes: [idx("PRIMARY", ["card_id"], true)],
    approx_rows: 3_400_000,
  },
];

const snapshot = (application: string, connector: string, environment: string, tables: TableSchema[]): SchemaSnapshot => ({
  application,
  connector,
  environment,
  imported_at: "2026-11-02T09:30:00Z",
  tables,
});

/** The latest import of each SQL connector, per environment. */
export const schemaSnapshots: SchemaSnapshot[] = ["prod", "uat"].flatMap((environment) => [
  snapshot("wallet", "wallet_db", environment, walletTables),
  snapshot("wallet", "ledger_db", environment, ledgerTables),
  snapshot("card_access", "access_db", environment, accessTables),
]);

/** What importing access_db returns: the backend caps the tables it reads. */
export const truncatedAccessSnapshot = (environment: string): SchemaSnapshot => ({
  ...snapshot("card_access", "access_db", environment, accessTables),
  truncated: "imported 1000 of 1,240 tables",
});

/** The wallet_db import in UAT is not there until the button is used. */
export const UNIMPORTED = { application: "wallet", connector: "wallet_db", environment: "uat" };
