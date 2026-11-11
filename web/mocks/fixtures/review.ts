// Author: @SaadZarook
import type { CatalogReview, TableReview } from "../../src/api/client";
import { cardAccessCatalog, walletCatalog } from "./catalog";

const both = ["prod", "uat"];

/** Tables only suggested: in an import, not in catalog.yaml. The wallet ones are also in the wallet_db import (catalog.ts). */
export const walletSuggestions: TableReview[] = [
  {
    connector: "wallet_db",
    schema: "dbo",
    table: "wallets",
    label: null,
    suggestion: {
      label: "master",
      reasons: ["one row per wallet, keyed by id", "no time column"],
      columns: { id: ["primary_key"], user_id: ["link"] },
    },
    imported_in: both,
  },
  {
    connector: "wallet_db",
    schema: "dbo",
    table: "currencies",
    label: null,
    suggestion: { label: "reference", reasons: ["about 42 rows and no time column"], columns: { code: ["primary_key"] } },
    imported_in: both,
  },
  {
    connector: "wallet_db",
    schema: "dbo",
    table: "topup_status_history",
    label: null,
    suggestion: {
      label: "audit",
      reasons: ["name ends in _history", "changed_at and status columns"],
      columns: { id: ["primary_key"], topup_id: ["link"], status: ["status"], changed_at: ["time"] },
      // As the backend suggests it: topup_id is named for topups' key.
      links: { topup_id: { connector: "wallet_db", table: "topups", column: "id" } },
    },
    imported_in: both,
  },
  {
    connector: "wallet_db",
    schema: "dbo",
    table: "user_provider_ids",
    label: null,
    suggestion: {
      label: "external_mapping",
      reasons: ["holds a provider's id for each user"],
      columns: { user_id: ["primary_key"], provider_user_id: ["external_key"] },
    },
    imported_in: both,
  },
];

/** What the review says about the confirmed wallet tables: topups is read by a probe and drifts in UAT. */
const walletConfirmed = (): TableReview[] =>
  walletCatalog.tables.map((t) => ({
    connector: t.connector,
    table: t.table,
    label: t.label,
    suggestion: null,
    imported_in: both,
    drift:
      t.table === "topups" ? [{ environment: "uat", message: "created_at is marked indexed, but no index starts with it" }] : [],
    used_by: t.table === "topups" ? ["topup_completed"] : [],
  }));

/** The wallet review before anything is confirmed. */
export const walletReviewTables = (): TableReview[] => [...walletConfirmed(), ...walletSuggestions];

/** Card access has nothing to review. */
export const cardAccessReview: CatalogReview = {
  application: "card_access",
  unconfirmed: 0,
  drifted: 0,
  tables: cardAccessCatalog.tables.map((t) => ({
    connector: t.connector,
    table: t.table,
    label: t.label,
    suggestion: null,
    imported_in: both,
    drift: [],
    used_by: [],
  })),
};
