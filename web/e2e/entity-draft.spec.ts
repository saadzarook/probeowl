// Author: @SaadZarook
import { expect, test } from "@playwright/test";
import { parse } from "yaml";
import { cardAccessCatalog, schemaSnapshots, walletCatalog } from "../mocks/fixtures/catalog";
import { DEFAULT_MAX_ROWS, entityTitle, entityYaml, lookupSql, type EntityDraft } from "../src/entityDraft";
import {
  defaultRootKey,
  identifierCandidates,
  importedTables,
  isGeneric,
  linkedLocation,
  pretickedIdentifiers,
  rootLocation,
  suggestedLinks,
  tableReference,
  type ImportedTable,
} from "../src/followKeys";
import { failuresByLocation, linkFix } from "../src/locationChecks";

// The entity wizard's logic, with no page: the YAML it writes and the keys it offers to follow.

const prod = (application: string) => importedTables(schemaSnapshots.filter((s) => s.application === application && s.environment === "prod"));
const wallet = prod("wallet");
const access = prod("card_access");
const table = (tables: ImportedTable[], name: string) => {
  const found = tables.find((t) => t.table === name);
  if (!found) throw new Error(`no table ${name} in the fixtures`);
  return found;
};
const offered = (links: ReturnType<typeof suggestedLinks>) => links.map((l) => `${l.from.location}.${l.from.column} > ${l.to.connector}.${l.to.table}.${l.to.column} (${l.reason})`);

test("common column names are generic; distinctive ones are not", () => {
  for (const name of ["id", "status", "type", "created_at", "updated_at", "posted_at", "valid_on", "is_active", "has_card", "currency", "name"]) {
    expect(isGeneric(name), name).toBe(true);
  }
  for (const name of ["provider_payment_id", "visitor_profile_id", "card_id", "ledger_txn_id", "user_id"]) expect(isGeneric(name), name).toBe(false);
});

test("the root location exposes its key as the root key, and <entity>_id when the key is just id", () => {
  const topups = table(wallet, "topups");
  expect(defaultRootKey("topup", topups)).toBe("topup_id");
  const root = rootLocation(topups, wallet, walletCatalog.tables, "topup_id");
  expect(root.table).toBe("topups"); // dbo is wallet_db's usual schema: left out
  expect(lookupSql(root, "topup_id")).toContain("SELECT id AS topup_id, user_id,");
  expect(lookupSql(root, "topup_id")).toMatch(/FROM topups WHERE id = :topup_id$/);
  expect(root.columns.find((c) => c.column === "customer_email")?.pii).toBe(true); // the catalog marks it
});

test("following keys offers catalog links on any connector and distinctive names, never generic ones", () => {
  const root = rootLocation(table(wallet, "topups"), wallet, walletCatalog.tables, "topup_id");
  const links = offered(suggestedLinks([root], wallet, walletCatalog.tables));
  expect(links).toContain("topups.ledger_txn_id > ledger_db.ledger_entries.txn_id (catalog)"); // another connector
  expect(links).toContain("topups.id > wallet_db.outbox_events.aggregate_id (catalog)"); // the link points back
  expect(links).toContain("topups.user_id > wallet_db.wallets.user_id (same name)");
  expect(links).toContain("topups.id > wallet_db.topup_status_history.topup_id (table key)");
  // status, currency and id are in several tables: none is offered by name.
  expect(links.filter((l) => /\.(status|currency|id) \(same name\)/.test(l) || / > [^ ]*\.(status|currency) /.test(l))).toEqual([]);
});

test("a link on a column that is not unique is many, capped at 100 or a lower row ceiling", () => {
  const root = rootLocation(table(wallet, "topups"), wallet, walletCatalog.tables, "topup_id");
  const [toLedger] = suggestedLinks([root], wallet, walletCatalog.tables).filter((l) => l.to.table === "ledger_entries");
  if (!toLedger) throw new Error("no link to ledger_entries");
  const ledger = linkedLocation(toLedger, root, "topup_id", wallet, walletCatalog.tables, [root], 1000);
  expect(ledger).toMatchObject({ connector: "ledger_db", table: "ledger_entries", keyColumn: "txn_id", cardinality: "many", maxRows: DEFAULT_MAX_ROWS });
  expect(ledger?.keyFrom).toEqual({ location: "topups", field: "ledger_txn_id" });
  expect(linkedLocation(toLedger, root, "topup_id", wallet, walletCatalog.tables, [root], 50)?.maxRows).toBe(50);
});

test("a link keyed by the root key needs no key_from, and the YAML is a valid entity", () => {
  const profile = rootLocation(table(access, "visitor_profile"), access, cardAccessCatalog.tables, "visitor_profile_id");
  const [toCards] = suggestedLinks([profile], access, cardAccessCatalog.tables);
  expect(toCards).toMatchObject({ to: { table: "cards", column: "visitor_profile_id" }, reason: "catalog" });
  if (!toCards) throw new Error("no link to cards");
  const cards = linkedLocation(toCards, profile, "visitor_profile_id", access, cardAccessCatalog.tables, [profile], 1000);
  if (!cards) throw new Error("no cards location");
  expect(cards.keyFrom).toBeUndefined();
  const candidates = identifierCandidates([profile, cards], access, "visitor_profile_id");
  expect(candidates.find((c) => c.field === "card_id")?.unique).toBe(true); // unique in cards, a many location
  expect(candidates.find((c) => c.field === "email")?.pii).toBe(true);
  const draft: EntityDraft = {
    entity: "badge_holder",
    title: "Badge holder",
    owner: "access-team",
    rootKey: "visitor_profile_id",
    locations: [profile, cards],
    identifiers: [
      { name: "visitor_profile_id", unique: true, pii: false, pattern: "^VP\\d{8}$" },
      { name: "card_id", via: { location: "cards", field: "card_id" }, unique: true, pii: false, pattern: "" },
    ],
  };
  const text = entityYaml(draft);
  expect(text.startsWith("# Written by the entity wizard.")).toBe(true);
  const document = parse(text) as { root_key: string; identifiers: Record<string, unknown>; locations: Record<string, Record<string, unknown>> };
  const location = (name: string) => document.locations[name] ?? {};
  expect(document.root_key).toBe("visitor_profile_id");
  expect(document.identifiers).toEqual({
    visitor_profile_id: { pattern: "^VP\\d{8}$", unique: true },
    card_id: { via: "cards.card_id", unique: true },
  });
  expect(location("cards")).toMatchObject({
    using: "access_db",
    lookup: "SELECT card_id, visitor_profile_id, status, valid_to, access_group FROM cards WHERE visitor_profile_id = :visitor_profile_id",
    cardinality: "many",
    max_rows: 100,
  });
  expect(location("cards")["key_from"]).toBeUndefined();
  expect(location("visitor_profile")["pii"]).toEqual(["email"]);
});

test("a title left blank is the id in words, never an empty title", () => {
  expect(entityTitle({ entity: "visitor_profile", title: "" })).toBe("Visitor profile");
  expect(entityTitle({ entity: "topup", title: "   " })).toBe("Topup");
  expect(entityTitle({ entity: "visitor_profile", title: "Visitor" })).toBe("Visitor");
  const blank: EntityDraft = { entity: "visitor_profile", title: "", owner: "t", rootKey: "visitor_profile_id", locations: [], identifiers: [] };
  expect((parse(entityYaml(blank)) as { title: string }).title).toBe("Visitor profile");
});

test("a location keyed by a renamed root key exposes its key as the root key; only such locations offer identifiers", () => {
  const profile = rootLocation(table(access, "visitor_profile"), access, cardAccessCatalog.tables, "badge_holder_id");
  const [toCards] = suggestedLinks([profile], access, cardAccessCatalog.tables);
  if (!toCards) throw new Error("no link to cards");
  const cards = linkedLocation(toCards, profile, "badge_holder_id", access, cardAccessCatalog.tables, [profile], 1000);
  if (!cards) throw new Error("no cards location");
  expect(lookupSql(cards, "badge_holder_id")).toBe(
    "SELECT card_id, visitor_profile_id AS badge_holder_id, status, valid_to, access_group FROM cards WHERE visitor_profile_id = :badge_holder_id",
  );
  const root = rootLocation(table(wallet, "topups"), wallet, walletCatalog.tables, "topup_id");
  const [toLedger] = suggestedLinks([root], wallet, walletCatalog.tables).filter((l) => l.to.table === "ledger_entries");
  if (!toLedger) throw new Error("no link to ledger_entries");
  const ledger = linkedLocation(toLedger, root, "topup_id", wallet, walletCatalog.tables, [root], 1000);
  if (!ledger) throw new Error("no ledger location");
  // ledger_entries is keyed from the top-up, not by the root key: its fields cannot find a top-up.
  const where = new Set(identifierCandidates([root, ledger], wallet, "topup_id").map((c) => c.location));
  expect([...where]).toEqual(["topups"]);
});

test("a table outside its connector's usual schema is named with its schema", () => {
  const tables: ImportedTable[] = [
    { connector: "db", schema: "dbo", table: "a", columns: ["id"], primaryKey: ["id"], unique: [] },
    { connector: "db", schema: "dbo", table: "b", columns: ["id"], primaryKey: ["id"], unique: [] },
    { connector: "db", schema: "audit", table: "c", columns: ["id"], primaryKey: ["id"], unique: [] },
  ];
  expect(tables.map((t) => tableReference(t, tables))).toEqual(["a", "b", "audit.c"]);
});

test("a location keyed by the root key brings its unique, non-PII columns as identifiers; a unique email stays unticked", () => {
  const visitors: ImportedTable = { connector: "access_db", schema: null, table: "visitor_profile", columns: ["id", "email"], primaryKey: ["id"], unique: [] };
  const cards: ImportedTable = {
    connector: "access_db", schema: null, table: "cards",
    columns: ["card_id", "visitor_profile_id", "holder_email", "status"],
    primaryKey: ["card_id"], unique: [["holder_email"]],
  }; // prettier-ignore
  const tables = [visitors, cards];
  const catalog = [{ connector: "access_db", table: "cards", label: "master" as const, owner: "t", columns: { holder_email: { roles: ["pii" as const] } } }];
  const root = rootLocation(visitors, tables, catalog, "visitor_id");
  const link = { from: { location: root.name, column: "id" }, to: { connector: "access_db", table: "cards", column: "visitor_profile_id" }, reason: "table key" as const };
  const added = linkedLocation(link, root, "visitor_id", tables, catalog, [root], undefined);
  if (!added) throw new Error("cards was not added");
  const ticked = pretickedIdentifiers([added], tables, "visitor_id", [{ name: "visitor_id", unique: true, pii: false, pattern: "" }]);
  expect(ticked).toEqual([{ name: "card_id", via: { location: "cards", field: "card_id" }, unique: true, pii: false, pattern: "" }]);
  // Offered all the same, with why it is unique and marked PII, so a person who ticks it keeps it masked.
  const email = identifierCandidates([added], tables, "visitor_id").find((c) => c.field === "holder_email");
  expect(email).toEqual({ location: "cards", field: "holder_email", unique: true, pii: true, because: "a unique index on cards" });
  // The location records which column it follows, for the catalog fix.
  expect(added.follows).toEqual({ connector: "access_db", table: "visitor_profile", column: "id" });
});

test("a refused save's failures go to the location they name; the time filter's fix marks the followed link", () => {
  const { byLocation, general } = failuresByLocation(
    [
      { field: "Time filter: topups", message: "topups is a Transaction table" },
      { field: "entity", message: "choose a new name" },
      { field: "Read-only SQL: nowhere", message: "not a location of this draft" },
    ],
    ["wallets", "topups"],
  );
  expect(byLocation.get("topups")).toEqual([{ check: "Time filter", message: "topups is a Transaction table" }]);
  expect(general.map((f) => f.check)).toEqual(["entity", "Read-only SQL: nowhere"]);

  const tables = wallet;
  const wallets = rootLocation(table(tables, "wallets"), tables, walletCatalog.tables, "wallet_id");
  const link = { from: { location: "wallets", column: "user_id" }, to: { connector: "wallet_db", table: "topups", column: "user_id" }, reason: "same name" as const };
  const topups = linkedLocation(link, wallets, "wallet_id", tables, walletCatalog.tables, [wallets], 1000);
  if (!topups) throw new Error("topups was not added");
  const fix = linkFix(topups, byLocation.get("topups") ?? [], walletCatalog.tables, tables);
  expect(fix?.column).toBe("user_id");
  expect(fix?.entry.columns["user_id"]).toEqual({ roles: ["link"], links_to: { connector: "wallet_db", table: "wallets", column: "user_id" } });
  // Every other column of the entry is kept as it was.
  expect(fix?.entry.columns["ledger_txn_id"]).toEqual(walletCatalog.tables[0]?.columns["ledger_txn_id"]);
  // No time filter failure, no fix.
  expect(linkFix(topups, [{ check: "Index check", message: "full scan" }], walletCatalog.tables, tables)).toBeUndefined();
});
