// Author: @SaadZarook
import { expect, test } from "@playwright/test";
import { parse } from "yaml";
import { cardAccessCatalog, schemaSnapshots, walletCatalog } from "../mocks/fixtures/catalog";
import { checkedName, DEFAULT_MAX_FANOUT, DEFAULT_MAX_ROWS, draftProblems, entityTitle, entityYaml, lookupSql, withFanOuts, type DraftLocation, type EntityDraft } from "../src/entityDraft";
import {
  defaultRootKey,
  identifierCandidates,
  importedTables,
  isGeneric,
  linkedLocation,
  pretickedIdentifiers,
  reasonText,
  rootLocation,
  suggestedLinks,
  tableReference,
  type ImportedTable,
} from "../src/followKeys";
import { failuresByLocation, linkFix } from "../src/locationChecks";

// The entity graph's logic, with no page: the YAML it writes and the keys it offers to follow.

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
  expect(text.startsWith("# Written by the entity graph.")).toBe(true);
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
      // As the backend sends them: each failed check names its check and location (#108).
      { field: "Time filter: topups", check: "Time filter", location: "topups", message: "topups is a Transaction table" },
      { field: "entity", message: "choose a new name" },
      { field: "Read-only SQL: nowhere", check: "Read-only SQL", location: "nowhere", message: "not a location of this draft" },
    ],
    ["wallets", "topups"],
  );
  expect(byLocation.get("topups")).toEqual([{ check: "Time filter", message: "topups is a Transaction table" }]);
  expect(general.map((f) => f.check)).toEqual(["entity", "Read-only SQL"]);

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

const small = (connector: string, name: string, columns: string[], primaryKey: string[] = ["id"]): ImportedTable => ({ connector, schema: null, table: name, columns, primaryKey, unique: [] });

test("a column named for another table's key is offered in both directions, saying whose key", () => {
  const orders = small("shop", "orders", ["id", "customer_id"]);
  const customers = small("crm", "customers", ["id", "email"]);
  const tables = [orders, customers];
  // From orders, its customer_id leads to customers; from customers, its id leads back to orders.
  const down = suggestedLinks([rootLocation(orders, tables, [], "order_id")], tables, []);
  expect(offered(down)).toEqual(["orders.customer_id > crm.customers.id (table key)"]);
  expect(down.map((l) => reasonText(l, "shop"))).toEqual(["named for customers' key"]);
  const up = suggestedLinks([rootLocation(customers, tables, [], "customer_id")], tables, []);
  expect(offered(up)).toEqual(["customers.id > shop.orders.customer_id (table key)"]);
  expect(up.map((l) => reasonText(l, "crm"))).toEqual(["named for customers' key"]);
  // Not a key of several columns: one column doesn't name it.
  expect(suggestedLinks([rootLocation(orders, tables, [], "order_id")], [orders, small("crm", "customers", ["id", "region"], ["id", "region"])], [])).toEqual([]);
});

test("a shared column name says which connector the other table is on", () => {
  const here = small("wallet_db", "topups", ["id", "ledger_txn_id"]);
  const there = small("ledger_db", "entries", ["entry", "ledger_txn_id"], ["entry"]);
  const beside = small("wallet_db", "audits", ["entry", "ledger_txn_id"], ["entry"]);
  const links = suggestedLinks([rootLocation(here, [here, there, beside], [], "topup_id")], [here, there, beside], []);
  expect(links.map((l) => `${l.to.table}: ${reasonText(l, "wallet_db")}`)).toEqual(["entries: same column name on ledger_db", "audits: same column name, also on wallet_db"]);
});

test("a key from a many location is a fan-out, under its parent, capped, and only one level deep", () => {
  const people = small("db", "persons", ["id", "name"]);
  const badges = { ...small("db", "badges", ["badge_id", "person_id"], ["badge_id"]), unique: [["badge_id"]] };
  const doors = small("doors_db", "door_events", ["event_id", "badge_id"], ["event_id"]);
  const tables = [people, badges, doors];
  const root = rootLocation(people, tables, [], "person_id");
  const [toBadges] = suggestedLinks([root], tables, []);
  const badge = toBadges && linkedLocation(toBadges, root, "person_id", tables, [], [root], 1000);
  if (!badge) throw new Error("no badges");
  expect([badge.cardinality, badge.maxFanout]).toEqual(["many", undefined]); // keyed by the root key itself
  const toDoors = suggestedLinks([root, badge], tables, []).find((l) => l.to.table === "door_events");
  const door = toDoors && linkedLocation(toDoors, badge, "person_id", tables, [], [root, badge], 1000);
  if (!door) throw new Error("no door_events");
  expect(door.maxFanout).toBe(DEFAULT_MAX_FANOUT);
  expect(checkedName(door)).toBe("badges.door_events");
  const draft: EntityDraft = { entity: "person", title: "", owner: "t", rootKey: "person_id", locations: [root, badge, door], identifiers: [] };
  const { locations } = parse(entityYaml(draft)) as { locations: Record<string, { each?: Record<string, Record<string, unknown>> }> };
  expect(Object.keys(locations)).toEqual(["persons", "badges"]);
  const each = locations["badges"]?.each?.["door_events"] ?? {};
  expect(each).toMatchObject({ using: "doors_db", max_fanout: 10, cardinality: "many", lookup: "SELECT event_id, badge_id FROM door_events WHERE badge_id = :badge_id" });
  expect(each["key_from"]).toBeUndefined();
  expect(each["max_rows"]).toBeUndefined(); // not a field of a fan-out
  // Nothing is offered from a fan-out, and nothing keyed from one is accepted.
  expect(suggestedLinks([door], tables, [])).toEqual([]);
  const nested: DraftLocation = { ...root, name: "readers", keyFrom: { location: "door_events", field: "event_id" } };
  expect(draftProblems({ ...draft, locations: [...draft.locations, nested] }).join()).toContain("a fan-out can't have another");
  // It can't find a subject.
  expect(identifierCandidates([root, badge, door], tables, "person_id").some((c) => c.location === "door_events")).toBe(false);
});

test("a location changed to one row, or to many, changes those keyed from it", () => {
  const at = (name: string, cardinality: "one" | "many", from?: string): DraftLocation => ({ name, title: name, connector: "db", table: name, keyColumn: "k", cardinality, columns: [], ...(from ? { keyFrom: { location: from, field: "k" } } : {}) });
  const many = withFanOuts([at("a", "one"), at("b", "many", "a"), at("c", "one", "b")]);
  expect(many.map((l) => l.maxFanout)).toEqual([undefined, undefined, DEFAULT_MAX_FANOUT]);
  expect(withFanOuts(many.map((l) => (l.name === "b" ? { ...l, cardinality: "one" as const } : l))).map((l) => l.maxFanout)).toEqual([undefined, undefined, undefined]);
  // A cap a person set is kept.
  expect(withFanOuts(many.map((l) => (l.name === "c" ? { ...l, maxFanout: 3 } : l)))[2]?.maxFanout).toBe(3);
});

test("a fan-out's failed check shows on it, though the check names it under its parent", () => {
  const found = failuresByLocation([{ field: "Read-only SQL: badges.doors", message: "no", check: "Read-only SQL", location: "badges.doors" }], ["badges", "doors"], (checked) => (checked === "badges.doors" ? "doors" : checked));
  expect([...found.byLocation.keys()]).toEqual(["doors"]);
});
