// Author: @SaadZarook
import type { Entity, ProbeSummary } from "../../src/api/client";

export const topUpEntity: Entity = {
  entity: "topup",
  title: "Top-up",
  root_key: "topup_id",
  owner: "payments-team",
  identifiers: {
    topup_id: { pattern: "^tp_[0-9a-z]{6}$", unique: true, pii: false },
    provider_payment_id: { pattern: "^pay_[0-9a-z]{6}$", via: "record.provider_payment_id", unique: true, pii: false },
    ledger_txn_id: { pattern: "^lt_[0-9a-z]{6}$", via: "record.ledger_txn_id", unique: true, pii: false },
  },
  locations: {
    record: { title: "Top-up record", using: "wallet_db", lookup: "SELECT id AS topup_id FROM topups WHERE id = :topup_id", cardinality: "one" },
    payment: { title: "Provider payment", using: "checkout_api", key_from: "record.provider_payment_id", lookup: "GET /payments/{{provider_payment_id}}", cardinality: "one" },
    webhook: { title: "Webhook deliveries", using: "wallet_db", key_from: "record.provider_payment_id", lookup: "SELECT id, payment_id, event_type, received_at FROM provider_webhooks WHERE payment_id = :provider_payment_id", cardinality: "many", max_rows: 20 },
    ledger: { title: "Ledger entries", using: "ledger_db", key_from: "record.ledger_txn_id", lookup: "SELECT account, direction, amount, posted_at FROM ledger_entries WHERE txn_id = :ledger_txn_id", cardinality: "many", max_rows: 20 },
  },
};

export const walletProbes: ProbeSummary[] = [
  { name: "topup_completed", title: "Top-up completed", kind: "validate", entity: "topup", owner: "payments-team", description: "A top-up was captured, confirmed by webhook and posted to the ledger once." },
];
