// Author: @SaadZarook
import type { ComparedField } from "../../src/api/client";

const plain = (row: Omit<ComparedField, "masked">): ComparedField => ({ ...row, masked: false });

/** tp_91f2c7 against tp_91f2b9, as the dev stack's planted subjects compare. */
export const topUpDifferences: ComparedField[] = [
  plain({ location: "record", connector: "wallet_db", field: null, failing: "Found, 1 row", healthy: "Found, 1 row", result: "same" }),
  plain({ location: "record", connector: "wallet_db", field: "status", failing: "succeeded", healthy: "succeeded", result: "same" }),
  plain({ location: "record", connector: "wallet_db", field: "amount", failing: "500.00", healthy: "200.00", result: "expected_to_vary" }),
  plain({ location: "record", connector: "wallet_db", field: "payment_method", failing: "apple_pay", healthy: "card", result: "different" }),
  plain({ location: "record", connector: "wallet_db", field: "app_version", failing: "5.3.0", healthy: "5.2.4", result: "different" }),
  { location: "record", connector: "wallet_db", field: "customer_email", failing: null, healthy: null, result: "different", masked: true },
  plain({ location: "payment", connector: "checkout_api", field: null, failing: "Found, 1 row", healthy: "Found, 1 row", result: "same" }),
  plain({ location: "payment", connector: "checkout_api", field: "status", failing: "captured", healthy: "captured", result: "same" }),
  plain({ location: "webhook", connector: "wallet_db", field: null, failing: "Found, 2 rows", healthy: "Found, 1 row", result: "different" }),
  plain({ location: "ledger", connector: "ledger_db", field: null, failing: "Missing, 0 rows", healthy: "Found, 2 rows", result: "different" }),
  plain({ location: "ledger", connector: "ledger_db", field: "direction", failing: null, healthy: ["credit", "debit"], result: "not_compared" }),
];

/** Every field the same, so no row leads the table. */
export const allSame: ComparedField[] = [
  plain({ location: "record", connector: "wallet_db", field: null, failing: "Found, 1 row", healthy: "Found, 1 row", result: "same" }),
  plain({ location: "record", connector: "wallet_db", field: "status", failing: "succeeded", healthy: "succeeded", result: "same" }),
];

/** Both sides errored: nothing to say about the data. */
export const allNotCompared: ComparedField[] = [
  plain({ location: "record", connector: "wallet_db", field: null, failing: "Error, 0 rows", healthy: "Error, 0 rows", result: "not_compared" }),
  plain({ location: "record", connector: "wallet_db", field: "status", failing: null, healthy: null, result: "not_compared" }),
];

/** One location the same, another errored on both sides. */
export const someNotCompared: ComparedField[] = [
  plain({ location: "record", connector: "wallet_db", field: null, failing: "Found, 1 row", healthy: "Found, 1 row", result: "same" }),
  plain({ location: "ledger", connector: "ledger_db", field: null, failing: "Error, 0 rows", healthy: "Error, 0 rows", result: "not_compared" }),
];

/** A masked field after owl serve restarted: its fingerprints went with the session. */
export const afterARestart: ComparedField[] = [
  { location: "record", connector: "wallet_db", field: "customer_email", failing: null, healthy: null, result: "not_compared", masked: true },
];
