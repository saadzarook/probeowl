// Author: @SaadZarook
import { stringify } from "yaml";
import { parse } from "yaml";
import { delay, http, HttpResponse } from "msw";
import type { ApplicationSummary, CatalogChange, CatalogReview, CatalogTable, TableReview, DraftConnection, KillSwitch, KillTarget, ProbeChange, SaveApplication, SaveConnector, SaveEntity, SaveSecret, SaveProbe, SetKillSwitch, StartRun } from "../src/api/client";
import type { CompareRequest } from "../../plugins/compare_files/web/types";
import { compareFilesManifest, compareFilesResult } from "./fixtures/compareFiles";
import type { HealthyAttempt, HealthyComparison, Run, RunbookView, StepInRun } from "../src/api/client";
import { CONFIGURATION_HEADER } from "../src/api/configuration";
import { detectIdentifier } from "../src/identifiers";
import {
  ambiguousEmailRun,
  applications,
  cardAccessConnectors,
  payoutsConnectors,
  walletConnectors,
  cardAccessCatalog,
  cardAccessReview,
  draftRun,
  passingReport,
  piiReport,
  timeFilterReport,
  unreadableReport,
  cardAccessProbes,
  failedValidateRun,
  inProduction,
  inactiveVisitorRun,
  plantedCardRun,
  runningCardRun,
  runningValidateRun,
  skippedCardRun,
  stoppedCardRun,
  lateValidateRun,
  stoppedValidateRun,
  skippedValidateRun,
  schemaSnapshots,
  truncatedAccessSnapshot,
  UNIMPORTED,
  topUpEntity,
  unknownVisitorRun,
  visitorEntity,
  walletCatalog,
  walletProbes,
  walletReviewTables,
  addedConnectorSnapshot,
  topUpDifferences,
  afterARestart,
  allSame,
  allNotCompared,
  someNotCompared,
  flaggedRunbook,
  outboxResult,
  stepsInRun,
  topUpNotCredited,
  walletRunPages,
  currentUser,
} from "./fixtures";

/** A started run reports `running` for this many polls, then a finished run. */
export const POLLS_BEFORE_FINISHED = 2;

/** Snapshots imported since the page loaded, keyed by application/connector/environment. */
const imported = new Map<string, unknown>();
const importKey = (application: string, connector: string, environment: string) => `${application}/${connector}/${environment}`;
/** How long the mocked import takes, so the progress state can be seen. */
export const IMPORT_DELAY_MS = 600;

/** Tables written to catalog.yaml since the page loaded, by application. */
const written = new Map<string, CatalogTable[]>();
/** How many times catalog.yaml changed since the page loaded, by application; a preview's base names the version it saw. */
const versions = new Map<string, number>();
const baseOf = (application: string) => `sha256:mock-${versions.get(application) ?? 0}`;
/** A confirm of this table finds catalog.yaml edited by someone else after its preview, once. */
const CONFLICTS_ONCE = new Set(["user_provider_ids", "topup_conflict"]);
const FAILS_ONCE = new Set(["visitor_cards"]);
const killSwitches: KillSwitch[] = [];
let killSwitchUnknown = false;

function sameKillTarget(a: KillTarget, b: KillTarget): boolean {
  return a.scope === b.scope && (a.application ?? null) === (b.application ?? null) && (a.connector ?? null) === (b.connector ?? null);
}

function stoppedBy(application: string, connector: string): string | undefined {
  const found = killSwitches.find(
    (s) => s.scope === "global" || (s.application === application && (s.scope === "application" || s.connector === connector)),
  );
  return found && `stopped by the kill switch: ${found.reason}`;
}
const KEPT_COMMENT = "# A comment only the real file has.";

// mock-no-catalog: Wallet as owl init leaves it, its schema imported but no catalog.yaml yet.
const noCatalog = (application: string) => application === "wallet" && localStorage.getItem("mock-no-catalog") !== null;

const catalogOf = (application: string): CatalogTable[] | undefined => {
  const added = written.get(application) ?? [];
  const base = noCatalog(application) ? (added.length > 0 ? [] : undefined) : { wallet: walletCatalog, card_access: cardAccessCatalog }[application]?.tables;
  if (!base) return undefined;
  return [...base.filter((t) => !added.some((a) => a.connector === t.connector && a.table === t.table)), ...added];
};

const KEY_ROLES = ["primary_key", "link", "external_key"];

/**
 * A new entity's failed save checks, each named "check: location" as the backend names them: a lookup by a
 * column with no index (here: status) fails the index check, and a Transaction, Log or Audit table read by a
 * column the catalog does not call a key fails the time filter, as rules.py's would.
 */
function entityFailures(application: string, text: string): { field: string; message: string }[] {
  const entity = parse(text) as { locations?: Record<string, { using?: string; lookup?: string }> };
  const failures: { field: string; message: string }[] = [];
  for (const [name, location] of Object.entries(entity.locations ?? {})) {
    const read = /FROM (?:\w+\.)?(\w+) WHERE (\w+) = :/.exec(location.lookup ?? "");
    if (!read) continue;
    const [, table = "", column = ""] = read;
    if (column === "status") {
      failures.push({ field: `Index check: ${name}`, message: `the lookup would read all of ${table}: no index on status` });
      continue;
    }
    const entry = catalogOf(application)?.find((t) => t.connector === location.using && t.table === table);
    const time = Object.entries(entry?.columns ?? {}).find(([, c]) => c.roles.includes("time"))?.[0];
    if (!entry || !["transaction", "log", "audit"].includes(entry.label) || time === undefined) continue;
    if (entry.columns[column]?.roles.some((role) => KEY_ROLES.includes(role))) continue;
    const label = entry.label.charAt(0).toUpperCase() + entry.label.slice(1);
    failures.push({
      field: `Time filter: ${name}`,
      message: `${table} is a ${label} table: filter on its time column ${time} from a parameter (for example ${time} >= :since), or look rows up by a key (a primary key, link or external key column = :parameter, or IN a short list)`,
    });
  }
  return failures;
}

function reviewOf(application: string): CatalogReview | undefined {
  const base = baseReviewOf(application);
  const added = addedImports.get(application) ?? [];
  if (!base || added.length === 0) return base;
  // A connector added through Add connection: its tables suggested, until each is confirmed.
  const confirmed = catalogOf(application) ?? [];
  const labelOf = (connector: string, table: string) => confirmed.find((c) => c.connector === connector && c.table === table)?.label ?? null;
  const tables = added.flatMap(({ connector, environment }): TableReview[] => [
    {
      connector,
      schema: null,
      table: "visitor_profile",
      imported_in: [environment],
      label: labelOf(connector, "visitor_profile"),
      suggestion: { label: "master", reasons: ["one row per visitor, keyed by id"], columns: { id: ["primary_key"], email: ["pii"] } },
    },
    {
      connector,
      schema: null,
      table: "cards",
      imported_in: [environment],
      label: labelOf(connector, "cards"),
      suggestion: { label: "transaction", reasons: ["many rows per visitor", "valid_to looks like its time column"], columns: { card_id: ["primary_key"], valid_to: ["time"] } },
    },
  ]);
  return { ...base, tables: [...base.tables, ...tables], unconfirmed: base.unconfirmed + tables.filter((t) => t.label === null).length };
}

function baseReviewOf(application: string): CatalogReview | undefined {
  if (application === "card_access") return cardAccessReview;
  // As owl serve answers: an application with nothing imported or confirmed has an empty review.
  if (application !== "wallet") return applications.some((a) => a.name === application) ? { application, tables: [], unconfirmed: 0, drifted: 0 } : undefined;
  const confirmed = catalogOf(application) ?? [];
  const tables = walletReviewTables().map((t) => ({ ...t, label: confirmed.find((c) => c.table === t.table)?.label ?? null }));
  return {
    application,
    tables,
    unconfirmed: tables.filter((t) => t.label === null && t.suggestion !== null).length,
    drifted: tables.filter((t) => (t.drift ?? []).length > 0).length,
  };
}

const yamlLines = (t: CatalogTable): string[] => [
  `  - connector: ${t.connector}`,
  `    table: ${t.table}`,
  `    label: ${t.label}`,
  `    owner: ${t.owner}`,
  ...(t.description ? [`    description: ${t.description}`] : []),
  "    columns:",
  ...Object.entries(t.columns).flatMap(([name, c]) => [
    `      ${name}:`,
    `        roles: [${c.roles.join(", ")}]`,
    ...(c.indexed ? ["        indexed: true"] : []),
    ...((c.values ?? []).length > 0 ? [`        values: [${(c.values ?? []).join(", ")}]`] : []),
    ...(c.links_to ? [`        links_to: {${Object.entries(c.links_to).map(([k, v]) => `${k}: ${String(v)}`).join(", ")}}`] : []),
  ]),
];

/** A unified diff of the entry replacing the table's confirmed one, or appended to the file. */
function diffFor(application: string, entry: CatalogTable): string {
  const old = catalogOf(application)?.find((t) => t.connector === entry.connector && t.table === entry.table);
  const path = `${application.replace(/_/g, "-")}/catalog.yaml`;
  const removed = old ? yamlLines(old).map((l) => `-${l}`) : [];
  const added = yamlLines(entry).map((l) => `+${l}`);
  return [`--- a/${path}`, `+++ b/${path}`, `@@ tables @@`, ...removed, ...added].join("\n");
}

const polls = new Map<string, { seen: number; request: StartRun; fromRun?: string }>();
const comparisons = new Map<string, { attempt: HealthyAttempt; healthy?: string; seen: number }>();
const failedOnce = new Set<string>();
let nextRun = 1;

/**
 * As owl serve answers: with how many times it reloaded its configuration. localStorage "mock-configuration"
 * stands for an owl reload run in a terminal while the page is open.
 */
function json(body: Parameters<typeof HttpResponse.json>[0], init?: ResponseInit) {
  const headers = new Headers(init?.headers);
  headers.set(CONFIGURATION_HEADER, localStorage.getItem("mock-configuration") ?? "0");
  return HttpResponse.json(body, { ...init, headers });
}

function problem(status: number, title: string, detail: string, errors?: { field: string; message: string }[]) {
  return json({ type: "about:blank", title, status, detail, errors }, {
    status,
    headers: { "Content-Type": "application/problem+json" },
  });
}

/** A draft test run is polled under this probe name. */
const DRAFT_PREFIX = "draft:";

interface DraftBody {
  probe: string;
  entity?: string | null;
  environment?: string | null;
}

/** The save checks the mocks report for a draft: unreadable YAML, a time filter missing on topups, or an unmasked email. */
function checksOf(body: DraftBody) {
  let name: string | null;
  try {
    const parsed: unknown = parse(body.probe);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("it is not a mapping");
    const probe = (parsed as { probe?: unknown }).probe;
    name = typeof probe === "string" ? probe : null;
  } catch (e) {
    return unreadableReport(e instanceof Error ? e.message.split("\n")[0] ?? "" : "");
  }
  const entity = body.entity ?? "";
  if (/FROM topups WHERE status = :status/.test(entity)) return timeFilterReport(name);
  if (/SELECT customer_email/.test(entity)) return piiReport(name);
  return passingReport(name);
}

const knownEnvironment = (application: string, environment: string | null | undefined) =>
  environment === undefined || environment === null || applications.find((a) => a.name === application)?.environments.some((e) => e.name === environment) === true;

function entitiesOf(application: string) {
  if (application === "wallet") return [topUpEntity];
  return application === "card_access" ? [visitorEntity] : [];
}

/** The planted card-access cases, by what was searched; anything else is an unknown visitor. */
function cardRun(id: string, request: StartRun, running: boolean): Run {
  const identifier = request.identifier ?? detectIdentifier(visitorEntity, request.value) ?? "visitor_profile_id";
  const value = request.value;
  if (identifier === "email") return ambiguousEmailRun(id, identifier);
  if (value === "VP00008807") return inactiveVisitorRun(id, identifier, value);
  if (value === "VP00000042") return skippedCardRun(id, identifier, value);
  if (value === "VP00000043") return stoppedCardRun(id, identifier, value);
  if (["VP00012931", "C204518", "C198220", "C177031"].includes(value)) {
    if (running) return runningCardRun(id, identifier, value);
    return request.environment === "prod" ? inProduction(plantedCardRun(id, identifier, value)) : plantedCardRun(id, identifier, value);
  }
  return unknownVisitorRun(id, identifier, value);
}

function runFor(id: string, request: StartRun, running: boolean): Run {
  if (request.probe.startsWith(DRAFT_PREFIX)) return running ? runningValidateRun(id) : draftRun(id);
  if (request.probe === "visitor_cards") return cardRun(id, request, running);
  if (request.value === "tp_paused") return skippedValidateRun(id);
  if (request.value === "tp_halted") return stoppedValidateRun(id);
  if (request.value === "tp_lagunk" && !running) return lateValidateRun(id);
  return running ? runningValidateRun(id) : failedValidateRun(id);
}

/** How a comparison ends: tp_93b001 has no candidates, the closer match finds none that pass, and a pasted tp_93b001 does not pass. */
function outcomeOf(value: string, body: { narrow?: string | null; subject?: string | null }): { status: HealthyAttempt["status"]; healthy?: string; message?: string } {
  if (body.subject === "tp_999999") return { status: "could_not_run", message: "stopped by the kill switch on connector ledger_db: on fire" };
  if (body.subject) return { status: "compared", healthy: body.subject };
  if (value === "tp_93b001") return { status: "no_candidates", message: "the sample found no candidates; paste a known-good ID" };
  if (body.narrow) return { status: "none_passed", message: "none of the 3 candidates passed; paste a known-good ID" };
  return { status: "compared", healthy: "tp_91f2b9" };
}

function differencesOf(value: string, healthy: string | undefined) {
  if (healthy === "tp_allsam") return allSame;
  if (healthy === "tp_allnot") return allNotCompared;
  if (healthy === "tp_mixedd") return someNotCompared;
  if (value === "tp_restrt") return [...topUpDifferences.filter((d) => d.field !== "customer_email"), ...afterARestart];
  return topUpDifferences;
}

/** The knowledge base opens these by URL, so they must be there after a reload. */
const SEEDED_RUNS = ["run_demo", "run_scan", "run_noplan", "run_masked", "run_badsteps"];
function seedRuns() {
  for (const id of SEEDED_RUNS) {
    polls.set(id, {
      seen: POLLS_BEFORE_FINISHED,
      request: { application: "wallet", environment: "uat", probe: "topup_completed", value: "tp_91f2c7", investigation: { reason: "incident", reference: "WALLET-482" } },
    });
  }
}
function seedCardRun() {
  polls.set("run_card", {
    seen: POLLS_BEFORE_FINISHED,
    request: { application: "card_access", environment: "uat", probe: "visitor_cards", value: "VP00012931", identifier: "visitor_profile_id", investigation: { reason: "incident", reference: "SUP-1" } },
  });
}
const ticks = new Map<string, Set<string>>();
const stepResults = new Map<string, Map<string, StepInRun["result"]>>();
const failedStepsOnce = new Set<string>();
let runbooksFailedOnce = false;

function matchingRunbooks(url: URL): RunbookView[] {
  const q = url.searchParams.get("q")?.toLowerCase();
  const entity = url.searchParams.get("entity");
  const table = url.searchParams.get("table");
  const identifier = url.searchParams.get("identifier");
  return [topUpNotCredited, flaggedRunbook].filter((runbook) => {
    if (q && !runbook.title.toLowerCase().includes(q)) return false;
    if (entity && runbook.applies_to.entity !== entity) return false;
    if (table && !(runbook.tables ?? []).some((t) => t.endsWith(`.${table}`))) return false;
    if (identifier && !JSON.stringify(runbook.steps).includes(`:${identifier}`)) return false;
    return true;
  });
}

// "mock-pii-key": the entity's key is PII (#66): subjects and matches show [masked], and what needs
// the real value later asks for it to be pasted.
const PII_KEY_REASON = "topup's key is PII and stored masked: paste its topup_id";
function piiKey(): boolean {
  return localStorage.getItem("mock-pii-key") === "1";
}
function masked<T extends { subject?: string | null }>(item: T): T {
  return piiKey() && item.subject ? { ...item, subject: "[masked]" } : item;
}

function comparisonOf(id: string, value: string): HealthyComparison {
  const choices = [{ name: "same_payment_method", title: "Same payment method" }];
  const base = { run: id, has_sample: value !== "tp_nosamp", choices: value === "tp_nosamp" ? [] : choices, failing_subject: piiKey() ? "[masked]" : value };
  const entry = comparisons.get(id);
  if (entry === undefined) return { ...base, attempt: null, differences: [] };
  if (entry.seen < 1) return { ...base, attempt: entry.attempt, differences: [] };
  const { attempt } = entry;
  const ended = outcomeOf(value, { narrow: attempt.narrow ?? null, subject: attempt.pasted ? (entry.healthy ?? "tp_999999") : null });
  if (ended.status !== "compared") return { ...base, attempt: { ...attempt, status: ended.status, message: ended.message ?? null }, differences: [] };
  return {
    ...base,
    attempt: { ...attempt, status: "compared", healthy_run: "run_healthy", runs: ["run_healthy"] },
    healthy_subject: entry.healthy ?? null,
    warning: entry.healthy === "tp_93b001" ? "tp_93b001 did not pass (failed), so its differences may not point at the cause" : null,
    differences: differencesOf(value, entry.healthy),
  };
}

/** A file's change as a save previews or writes it: written only when the preview's base is sent. */
function fileChange(path: string, text: string, base: string | null | undefined): CatalogChange {
  return {
    path,
    diff: `--- /dev/null\n+++ b/${path}\n@@ -0,0 +1 @@\n${text.split("\n").map((l) => `+${l}`).join("\n")}`,
    base: "absent",
    after: "sha256:mock-after",
    written: base !== undefined && base !== null,
  };
}

// A binding whose password this machine lacks, as a teammate's would: opt in with localStorage
// "mock-password-missing" = "<application>/<connector>/<environment>"; saving it fills it.
const savedPasswords = new Set<string>();
function passwordMissing(application: string, connector: string, environment: string): boolean {
  const key = `${application}/${connector}/${environment}`;
  return localStorage.getItem("mock-password-missing") === key && !savedPasswords.has(key);
}
function missingFor(application: string, environment: string): { connector: string; environment: string } | undefined {
  const [app, connector, env] = (localStorage.getItem("mock-password-missing") ?? "").split("/");
  if (app !== application || env !== environment || !connector) return undefined;
  return passwordMissing(application, connector, environment) ? { connector, environment } : undefined;
}
function passwordMissingProblem(connector: string, environment: string) {
  return HttpResponse.json(
    {
      type: "urn:probeowl:problem:password-missing",
      title: "Unprocessable Content",
      status: 422,
      detail: `${connector} needs your password for ${environment === "prod" ? "Production" : environment.toUpperCase()}: save it on this machine first`,
      connector,
      environment,
    },
    { status: 422, headers: { "Content-Type": "application/problem+json" } },
  );
}

// Connectors written through Add connection, and their imports, in this page's life.
const addedConnectors = new Set<string>();
const addedImports = new Map<string, { connector: string; environment: string }[]>();

// Applications created through first run in this page's life, listed after the examples.
const createdApplications: ApplicationSummary[] = [];

export const handlers = [
  // A config folder with no applications, as owl init has not been run yet.
  // "mock-slow-applications": the list is slow to come back once one is created, as on a busy machine.
  http.get("/api/applications", async () => {
    if (createdApplications.length > 0 && localStorage.getItem("mock-slow-applications")) await delay(1500);
    return json([...(localStorage.getItem("mock-no-applications") ? [] : applications), ...createdApplications]);
  }),

  http.get("/api/applications/:application", ({ params }) => {
    const found = applications.find((a) => a.name === params["application"]);
    return found ? json(found) : problem(404, "Not Found", "No such application.");
  }),

  http.get("/api/applications/:application/environments", ({ params }) => {
    const found = applications.find((a) => a.name === params["application"]);
    return found ? json(found.environments) : problem(404, "Not Found", "No such application.");
  }),

  http.get("/api/applications/:application/entities", ({ params }) =>
    json(entitiesOf(String(params["application"]))),
  ),

  http.get("/api/applications/:application/entities/:entity", ({ params }) => {
    const found = entitiesOf(String(params["application"])).find((e) => e.entity === params["entity"]);
    return found ? json(found) : problem(404, "Not Found", "No such entity.");
  }),

  http.get("/api/applications/:application/entities/:entity/yaml", ({ params }) => {
    const application = String(params["application"]);
    const found = entitiesOf(application).find((e) => e.entity === params["entity"]);
    if (!found) return problem(404, "Not Found", "No such entity.");
    const text = `${KEPT_COMMENT}\n${stringify(found, { lineWidth: 0 })}`;
    return json({ path: `${application}/entities/${found.entity}.yaml`, text });
  }),

  http.get("/api/applications/:application/probes/:probe/yaml", ({ params }) => {
    const application = String(params["application"]);
    const probes = application === "wallet" ? walletProbes : application === "card_access" ? cardAccessProbes : [];
    const found = probes.find((p) => p.name === params["probe"]);
    if (!found) return problem(404, "Not Found", "No such probe.");
    if (found.name === "visitor_cards" && FAILS_ONCE.delete(found.name)) return problem(500, "Internal Server Error", "The file could not be read.");
    const location = Object.keys(entitiesOf(application).find((e) => e.entity === found.entity)?.locations ?? {})[0] ?? "record";
    const body = found.kind === "validate" ? `steps:\n  - expect: count(${location}) >= 1\n` : `expect:\n  - count(${location}) >= 1\n`;
    const text = `${KEPT_COMMENT}\nprobe: ${found.name}\nkind: ${found.kind}\nentity: ${found.entity}\nowner: ${found.owner}\n${body}`;
    return json({ path: `${application.replace(/_/g, "-")}/probes/${found.name}.yaml`, text });
  }),

  http.post("/api/applications/:application/probes/checks", async ({ params, request }) => {
    const application = String(params["application"]);
    const body = (await request.json()) as DraftBody;
    if (!applications.some((a) => a.name === application)) return problem(404, "Not Found", "No such application.");
    if (!knownEnvironment(application, body.environment)) return problem(404, "Not Found", "No such environment.");
    const missing = missingFor(application, body.environment ?? "");
    if (missing) return passwordMissingProblem(missing.connector, missing.environment);
    return json(checksOf(body));
  }),

  http.post("/api/applications/:application/probes/test-runs", async ({ params, request }) => {
    const application = String(params["application"]);
    const body = (await request.json()) as DraftBody & Pick<StartRun, "value" | "identifier" | "investigation"> & { environment: string };
    if (!applications.some((a) => a.name === application)) return problem(404, "Not Found", "No such application.");
    if (!knownEnvironment(application, body.environment)) return problem(404, "Not Found", "No such environment.");
    const missing = missingFor(application, body.environment);
    if (missing) return passwordMissingProblem(missing.connector, missing.environment);
    const failed = checksOf(body).checks.filter((c) => c.status === "fail");
    if (failed.length > 0) {
      return problem(422, "Unprocessable Content", `${failed[0]?.check ?? "Probe"}: ${failed[0]?.message ?? ""}`, failed.map((c) => ({ field: c.location ? `${c.check}: ${c.location}` : c.check, message: c.message })));
    }
    const production = applications.find((a) => a.name === application)?.environments.find((e) => e.name === body.environment)?.production;
    if (production && !body.investigation.reference) return problem(422, "Unprocessable Content", "A reference is required in Production.");
    const id = `run_${nextRun++}`;
    polls.set(id, { seen: 0, request: { application, environment: body.environment, probe: `${DRAFT_PREFIX}draft`, value: body.value, investigation: body.investigation } });
    return json({ id, status: "running" }, { status: 202, headers: { Location: `/api/runs/${id}` } });
  }),

  // A new entity from the wizard. A lookup by a column with no index (here: status) fails the index
  // check, as the backend's plan check would; an entity that exists is refused.
  http.put("/api/applications/:application/entities/:entity", async ({ params, request }) => {
    const application = String(params["application"]);
    const entity = String(params["entity"]);
    const body = (await request.json()) as SaveEntity;
    if (!applications.some((a) => a.name === application)) return problem(404, "Not Found", "No such application.");
    if ([topUpEntity.entity, visitorEntity.entity].includes(entity)) {
      return problem(422, "Unprocessable Content", `${application} already has an entity ${entity}: change it with one of its probes in the Probe editor`, [{ field: "entity", message: "choose a new name" }]);
    }
    const failures = entityFailures(application, body.entity);
    if (failures.length > 0) {
      return problem(422, "Unprocessable Content", `the draft does not pass its save checks: ${failures[0]?.message ?? ""}`, failures);
    }
    const path = `${application.replace(/_/g, "-")}/entities/${entity}.yaml`;
    const report = { ...passingReport(null), entity };
    return json({
      path,
      diff: `--- a/${path}\n+++ b/${path}\n@@ -0,0 +1 @@\n${body.entity.split("\n").map((l) => `+${l}`).join("\n")}`,
      base: "absent",
      after: "sha256:mock-entity-after",
      written: body.base !== undefined && body.base !== null,
      report,
    });
  }),

  http.put("/api/applications/:application/probes/:probe", async ({ params, request }) => {
    const application = String(params["application"]);
    const probe = String(params["probe"]);
    const body = (await request.json()) as SaveProbe;
    if (!applications.some((a) => a.name === application)) return problem(404, "Not Found", "No such application.");
    if (!knownEnvironment(application, body.environment)) return problem(404, "Not Found", "No such environment.");
    const report = checksOf(body);
    const failed = report.checks.filter((c) => c.status === "fail");
    if (failed.length > 0) {
      return problem(422, "Unprocessable Content", `${failed[0]?.check ?? "Probe"}: ${failed[0]?.message ?? ""}`, failed.map((c) => ({ field: c.location ? `${c.check}: ${c.location}` : c.check, message: c.message })));
    }
    const production = applications.find((a) => a.name === application)?.environments.find((e) => e.name === body.environment)?.production === true;
    // As the backend does: a plan that cannot be read only warns in the preview.
    const planNotRun = {
      check: "ledger_db plan · Oracle",
      status: "warn" as const,
      message: "index use not checked: ledger_db has no binding in prod",
      location: "ledger",
      environment: body.environment,
      plan_not_run: true,
    };
    const shown = production ? { ...report, checks: [...report.checks, planNotRun], warnings: report.warnings + 1, plans_not_run: 1 } : report;
    const dir = application.replace(/_/g, "-");
    const diffOf = (path: string, text: string) => `--- a/${path}\n+++ b/${path}\n@@ -0,0 +1 @@\n${text.split("\n").map((l) => `+${l}`).join("\n")}`;
    const base = `sha256:mock-probe-${probe}`;
    const change = (isWritten: boolean): ProbeChange => {
      const entityChange =
        body.entity === undefined || body.entity === null
          ? null
          : { path: `${dir}/entities/${probe}.yaml`, diff: diffOf(`${dir}/entities/${probe}.yaml`, body.entity), base, after: "sha256:mock-entity-after", written: isWritten };
      return {
        path: `${dir}/probes/${probe}.yaml`,
        diff: diffOf(`${dir}/probes/${probe}.yaml`, body.probe),
        base,
        after: "sha256:mock-probe-after",
        written: isWritten,
        entity_change: entityChange,
        report: shown,
      };
    };
    if (body.base === undefined || body.base === null) return json(change(false));
    if (probe === "topup_conflict" && CONFLICTS_ONCE.delete(probe)) {
      return problem(409, "Conflict", `${dir}/probes/${probe}.yaml changed since its preview; preview the change again.`);
    }
    if (body.base !== base) return problem(409, "Conflict", "A file changed since its preview; preview the change again.");
    if (production && body.save_without_plan_check !== true) {
      return problem(422, "Unprocessable Content", "plan checks could not run in prod; to save without plan check, confirm it: that is recorded in the audit", [
        { field: "save_without_plan_check", message: "plan checks could not run in prod" },
      ]);
    }
    if (probe === "topup_broken") {
      const broken = `saving would break the folder: ${dir}/connectors/broken.yaml: not valid YAML`;
      return problem(422, "Unprocessable Content", broken, [{ field: `${dir}/connectors/broken.yaml`, message: "not valid YAML" }]);
    }
    if (probe === "topup_unchanged") return json(change(false));
    return json(change(true));
  }),

  http.get("/api/applications/:application/probes", ({ params, request }) => {
    const entity = new URL(request.url).searchParams.get("entity");
    const probes = params["application"] === "wallet" ? walletProbes : params["application"] === "card_access" ? cardAccessProbes : [];
    return json(probes.filter((p) => entity === null || p.entity === entity));
  }),

  // The person running owl serve: s.khan in the designs.
  http.get("/api/me", () => json({ kind: "person", name: currentUser.name, on_behalf_of: null })),

  http.get("/api/plugins", () => json(localStorage.getItem("mock-plugins-off") ? [] : [compareFilesManifest])),

  http.post("/api/plugins/compare_files/compare", async ({ request }) => {
    const body = (await request.json()) as CompareRequest;
    if (body.left.text.startsWith("invalid")) return problem(422, "Unprocessable Content", "a is not valid YAML (line 2)");
    return json(compareFilesResult(body));
  }),

  // mock-reload: "running" stands for a run in progress, "broken" for a folder owl serve would refuse,
  // "changed" for a connector edited since owl serve loaded the folder.
  http.post("/api/config/reload", () => {
    const state = localStorage.getItem("mock-reload");
    if (state === "running") return problem(409, "Conflict", "1 run is running; reload when they have finished");
    if (state === "broken") {
      return problem(422, "Unprocessable Content", "config has 1 errors, the first: wallet/connectors/ledger_db.yaml: environments.uat.host: Field required; run: owl check config");
    }
    return json({
      applications: 2,
      connectors: 5,
      probes: 2,
      applications_added: [],
      applications_removed: [],
      connectors_added: [],
      connectors_removed: [],
      connectors_changed: state === "changed" ? ["wallet/ledger_db"] : [],
      note: "Plugins, the port, the store and the log file change only when owl serve is restarted.",
    });
  }),

  http.get("/api/kill-switches", () => {
    if (killSwitchUnknown) return problem(503, "Service Unavailable", "the store cannot be read, so the kill switch cannot be checked");
    return json(killSwitches);
  }),

  http.post("/api/kill-switches", async ({ request }) => {
    const body = (await request.json()) as SetKillSwitch;
    const existing = killSwitches.find((s) => sameKillTarget(s, body));
    if (existing) return json(existing);
    const engaged: KillSwitch = { ...body, actor: { kind: "person", name: "s.khan" }, engaged_at: "2026-01-01T10:00:00Z" };
    killSwitches.push(engaged);
    return json(engaged);
  }),

  http.post("/api/kill-switches/release", async ({ request }) => {
    const body = (await request.json()) as SetKillSwitch;
    const index = killSwitches.findIndex((s) => sameKillTarget(s, body));
    if (index < 0) return problem(404, "Not Found", "No kill switch is on for that.");
    const [released] = killSwitches.splice(index, 1);
    return json(released);
  }),

  http.get("/api/applications/:application/connectors", ({ params, request }) => {
    // Orders stands for a store that cannot be read, so that state can be seen.
    killSwitchUnknown = params["application"] === "orders";
    const application = applications.find((a) => a.name === params["application"]);
    const name = new URL(request.url).searchParams.get("environment") ?? "";
    const environment = application?.environments.find((e) => e.name === name);
    // Onboarding's Dev environment stands for one the backend does not know, so the 404 can be seen.
    if (application === undefined || environment === undefined || (application.name === "onboarding" && environment.name === "dev")) {
      return problem(404, "Not Found", `There is no application and environment "${String(params["application"])}" / "${name}".`);
    }
    const list = { wallet: walletConnectors, card_access: cardAccessConnectors, payouts: payoutsConnectors }[application.name];
    const statuses = list?.(environment.name, environment.production) ?? [];
    return json(
      statuses.map((status) => {
        const reason = stoppedBy(application.name, status.connector);
        if (reason) return { ...status, health: "stopped" as const, reason };
        if (passwordMissing(application.name, status.connector, environment.name)) {
          return { ...status, secret: "missing" as const, health: "not_configured" as const, reason: `no password saved on this machine for ${environment.name}` };
        }
        return { ...status, secret: status.type === "rest" ? ("not_needed" as const) : ("saved" as const) };
      }),
    );
  }),

  http.post("/api/applications/:application/connectors/:connector/test", ({ params, request }) => {
    const application = String(params["application"]);
    const connector = String(params["connector"]);
    const environment = new URL(request.url).searchParams.get("environment") ?? "";
    const stopped = stoppedBy(application, connector);
    if (stopped) return problem(403, "Forbidden", stopped);
    // ledger_replica stands for a switch turned on elsewhere since the screen loaded, so the refusal can be seen.
    if (connector === "ledger_replica") return problem(403, "Forbidden", "stopped by the global kill switch: on fire");
    // payout_db stands for a database that cannot be reached, so that answer can be seen.
    const reason = connector === "payout_db" ? "the connection was refused or could not be made; check the database is up and the binding is right" : null;
    return json({ connector, environment, reachable: reason === null, reason, duration_ms: 42 });
  }),

  // First run: no applications until one is created; the store is new; the keychain works.
  http.get("/api/setup", () => {
    const none = localStorage.getItem("mock-no-applications") !== null;
    return json({
      applications: (none ? 0 : applications.length) + createdApplications.length,
      config_folder: "C:/Users/you/probeowl",
      store: { dialect: "sqlite", created_now: none },
      keychain: { available: true, reason: null },
    });
  }),

  http.put("/api/applications/:application", async ({ params, request }) => {
    const application = String(params["application"]);
    const body = (await request.json()) as SaveApplication;
    if (applications.some((a) => a.name === application) && localStorage.getItem("mock-no-applications") === null) {
      return problem(409, "Conflict", `an application named ${application} is already loaded`);
    }
    const environments = body.environments.map((e) => `  ${e.name}:\n    title: ${e.title}\n    production: ${String(e.production ?? false)}`);
    const text = `application: ${application}\ntitle: ${body.title}\nowner: ${body.owner}\nenvironments:\n${environments.join("\n")}`;
    const change = fileChange(`${application}/application.yaml`, text, body.base);
    if (change.written) {
      createdApplications.push({
        name: application,
        title: body.title,
        owner: body.owner,
        environments: body.environments.map((e) => ({ name: e.name, title: e.title, production: e.production ?? false })),
      });
    }
    return json(change);
  }),

  http.put("/api/applications/:application/connectors/:connector", async ({ params, request }) => {
    const application = String(params["application"]);
    const connector = String(params["connector"]);
    const body = (await request.json()) as SaveConnector;
    const binding = "host" in body.binding
      ? `    host: ${body.binding.host}\n    port: ${body.binding.port}\n    database: ${body.binding.database}\n    user: ${body.binding.user}\n    secret: keyring:probeowl/${connector}.${body.environment}`
      : `    base_url: ${body.binding.base_url}`;
    const text = `connector: ${connector}\ntype: ${body.type}\nenvironments:\n  ${body.environment}:\n${binding}`;
    const change = fileChange(`${application}/connectors/${connector}.yaml`, text, body.base);
    if (change.written) addedConnectors.add(`${application}/${connector}`);
    return json(change);
  }),

  // A password is saved with no body. wallet_db in Production has one already, so the question
  // before overwriting can be seen.
  http.put("/api/applications/:application/connectors/:connector/environments/:environment/secret", async ({ params, request }) => {
    const connector = String(params["connector"]);
    const environment = String(params["environment"]);
    const body = (await request.json()) as SaveSecret;
    if (connector === "wallet_db" && environment === "prod" && !body.replace) {
      return HttpResponse.json(
        {
          type: "urn:probeowl:problem:secret-exists",
          title: "Conflict",
          status: 409,
          detail: `a password is already saved as probeowl/${connector}.${environment}, perhaps for another application's connector named ${connector}; overwrite it only if it is this one's`,
          connector,
          environment,
        },
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      );
    }
    savedPasswords.add(`${String(params["application"])}/${connector}/${environment}`);
    return new HttpResponse(null, { status: 204 });
  }),

  // A draft connects unless its password is "wrong", so a failed test and its fixed reason can be seen.
  http.post("/api/applications/:application/connectors/test-draft", async ({ request }) => {
    const body = (await request.json()) as DraftConnection;
    // No binding: a saved one, tested with a new password before Change password saves it.
    if (!body.binding && !body.password) {
      return problem(422, "Unprocessable Content", "give the new password to test", [{ field: "password", message: "required when no binding is given" }]);
    }
    const sql = !body.binding || "host" in body.binding;
    if (sql && !body.password && !body.use_saved_password) {
      return problem(422, "Unprocessable Content", "give the password, or use the one saved on this machine", [
        { field: "password", message: "required unless use_saved_password is true" },
      ]);
    }
    const reason = body.password === "wrong" ? "authentication failed; check the binding's login and secret" : null;
    return json({ connector: body.connector, environment: body.environment, reachable: reason === null, reason, duration_ms: 31 });
  }),

  http.get("/api/applications/:application/catalog", ({ params }) => {
    const application = String(params["application"]);
    const tables = catalogOf(application);
    return tables ? json({ catalog: application, tables }) : problem(404, "Not Found", "This application has no catalog.yaml.");
  }),

  http.get("/api/applications/:application/catalog/review", ({ params }) => {
    const review = reviewOf(String(params["application"]));
    return review ? json(review) : problem(404, "Not Found", "This application has no catalog.yaml.");
  }),

  http.put("/api/applications/:application/catalog/tables/:connector/:table", async ({ params, request }) => {
    const application = String(params["application"]);
    const { entry, base } = (await request.json()) as { entry: CatalogTable; base?: string | null };
    if (entry.label === "transaction" && !Object.values(entry.columns).some((c) => c.roles.includes("time"))) {
      return problem(422, "Unprocessable Content", "a transaction table needs a time column");
    }
    const path = `${application.replace(/_/g, "-")}/catalog.yaml`;
    const change = (isWritten: boolean): CatalogChange => ({
      path,
      diff: diffFor(application, entry),
      // As owl serve says of a file that does not exist yet.
      base: catalogOf(application) === undefined ? "absent" : baseOf(application),
      after: `sha256:mock-${(versions.get(application) ?? 0) + 1}`,
      written: isWritten,
    });
    if (base === undefined || base === null) return json(change(false));
    if (CONFLICTS_ONCE.delete(entry.table)) versions.set(application, (versions.get(application) ?? 0) + 1);
    if (base !== (catalogOf(application) === undefined ? "absent" : baseOf(application))) {
      return problem(409, "Conflict", `${path} changed since its preview; preview the change again.`);
    }
    const result = change(true);
    written.set(application, [...(written.get(application) ?? []).filter((t) => t.table !== entry.table), entry]);
    versions.set(application, (versions.get(application) ?? 0) + 1);
    return json(result);
  }),

  http.get("/api/applications/:application/catalog/schema", ({ params, request }) => {
    const query = new URL(request.url).searchParams;
    const application = String(params["application"]);
    const connector = query.get("connector") ?? "";
    const environment = query.get("environment") ?? "";
    const found =
      imported.get(importKey(application, connector, environment)) ??
      (application === UNIMPORTED.application && connector === UNIMPORTED.connector && environment === UNIMPORTED.environment
        ? undefined
        : schemaSnapshots.find((s) => s.application === application && s.connector === connector && s.environment === environment));
    return found
      ? json(found)
      : problem(404, "Not Found", `${connector} has not been imported in ${environment}. Use Import from replica on the Tables screen.`);
  }),

  http.post("/api/applications/:application/catalog/imports", async ({ params, request }) => {
    const application = String(params["application"]);
    const { connector, environment } = (await request.json()) as { connector: string; environment: string };
    await delay(IMPORT_DELAY_MS);
    if (connector === "ledger_db" && environment === "uat") {
      return problem(502, "Bad Gateway", "ledger_db could not be read: the database did not answer within 30 s.");
    }
    if (connector === "checkout_api") {
      return problem(422, "Unprocessable Content", "checkout_api is a REST connector; only SQL connectors can be imported.");
    }
    if (addedConnectors.has(`${application}/${connector}`)) {
      const added = addedConnectorSnapshot(application, connector, environment);
      imported.set(importKey(application, connector, environment), added);
      addedImports.set(application, [...(addedImports.get(application) ?? []), { connector, environment }]);
      return json(added, { status: 201 });
    }
    const found =
      connector === "access_db" && application === "card_access"
        ? truncatedAccessSnapshot(environment)
        : schemaSnapshots.find((s) => s.application === application && s.connector === connector && s.environment === environment) ??
          schemaSnapshots.find((s) => s.application === application && s.connector === connector);
    if (!found) return problem(404, "Not Found", `No connector ${connector} in this application.`);
    const created = { ...found, environment, imported_at: new Date().toISOString() };
    imported.set(importKey(application, connector, environment), created);
    return json(created, { status: 201 });
  }),

  http.post("/api/runs", async ({ request }) => {
    const body = (await request.json()) as StartRun;
    if (!applications.some((a) => a.name === body.application)) {
      return problem(404, "Not Found", "No such application.");
    }
    const missing = missingFor(body.application, body.environment);
    if (missing) return passwordMissingProblem(missing.connector, missing.environment);
    // As the server does when the value is a time typed with an offset and its column stores none.
    const offset = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)$/i.exec(body.value)?.[1];
    if (offset !== undefined) {
      return problem(422, "Unprocessable Content", "topups.created_at is datetime2: this column stores time without an offset", [
        { field: "value", message: `this column stores time without an offset; enter it without ${offset}, in the column's own time` },
      ]);
    }
    const matches =
      body.application === "card_access"
        ? detectIdentifier(visitorEntity, body.value) !== undefined || body.identifier === "visitor_profile_id"
        : /^(tp|pay|lt)_[0-9a-z]{6}$/.test(body.value);
    if (!matches) {
      return problem(422, "Unprocessable Content", `No identifier of the entity matches "${body.value}".`, [
        { field: "identifier", message: "Pick which identifier this value is." },
      ]);
    }
    const id = `run_${nextRun++}`;
    const environment = applications.find((a) => a.name === body.application)?.environments.find((e) => e.name === body.environment);
    if (environment?.production && !body.investigation.reference) {
      return problem(422, "Unprocessable Content", "A reference is required in Production.");
    }
    polls.set(id, { seen: 0, request: body });
    return json({ id, status: "running" }, { status: 202, headers: { Location: `/api/runs/${id}` } });
  }),

  // Wallet in UAT has two pages of runs; every other application and environment has none.
  http.get("/api/runs", ({ request }) => {
    const query = new URL(request.url).searchParams;
    const wallet = query.get("application") === "wallet" && query.get("environment") === "uat";
    const page = query.get("before") === "page-2" ? 1 : 0;
    const runs = wallet ? (walletRunPages[page] ?? []) : [];
    return json({ runs, next: wallet && page === 0 ? "page-2" : null });
  }),

  http.get("/api/runs/:run_id", ({ params }) => {
    const id = String(params["run_id"]);
    if (SEEDED_RUNS.includes(id) && !polls.has(id)) seedRuns();
    if (id === "run_card") seedCardRun();
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    polls.set(id, { ...poll, seen: poll.seen + 1 });
    const running = poll.seen < POLLS_BEFORE_FINISHED;
    const { request } = poll;
    const run = runFor(id, request, running);
    const matches = piiKey() ? [...(run.matches ?? [])].sort((a, b) => a.subject.localeCompare(b.subject)).map(masked) : run.matches;
    return json(masked({
      ...run,
      matches,
      environment: request.environment,
      identifier_value: run.status === "ambiguous" ? run.identifier_value : request.value,
      investigation: request.investigation,
      from_run: poll.fromRun ?? null,
      subject: run.subject ?? (run.status === "passed" || run.status === "failed" ? request.value : null),
    }));
  }),

  http.get("/api/applications/:application/runbooks", ({ params, request }) => {
    const application = String(params["application"]);
    // Orders stands for a list that cannot be read the first time, so the retry can be seen.
    if (application === "orders" && !runbooksFailedOnce) {
      runbooksFailedOnce = true;
      return problem(500, "Internal Server Error", "The runbooks could not be read.");
    }
    return json(application === "wallet" ? matchingRunbooks(new URL(request.url)) : []);
  }),

  http.post("/api/applications/:application/runbooks/:runbook/steps/:step/copy", ({ params }) => {
    const runbook = [topUpNotCredited, flaggedRunbook].find((r) => r.runbook === params["runbook"]);
    const step = runbook?.steps.find((s) => s.id === params["step"]);
    if (step?.kind !== "write_query") return problem(422, "Unprocessable Content", "only a write query's copy is recorded here");
    return json({ text: `-- WRITE · no run · needs ${step.requires}\n${step.sql}`, unfilled: [] });
  }),

  http.get("/api/runs/:run_id/runbooks", ({ params }) => {
    const poll = polls.get(String(params["run_id"]));
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    // tp_nosugg finds nothing, and tp_500sug cannot be read the first time, so the retry can be seen.
    if (poll.request.value === "tp_500sug" && !failedStepsOnce.has(String(params["run_id"]))) {
      failedStepsOnce.add(String(params["run_id"]));
      return problem(500, "Internal Server Error", "The suggested runbooks could not be read.");
    }
    if (poll.request.application !== "wallet" || poll.request.value === "tp_nosugg") return json([]);
    return json([
      { runbook: topUpNotCredited, matched: ["ledger.missing"] },
      { runbook: flaggedRunbook, matched: ["webhook.ambiguous"] },
    ]);
  }),

  http.get("/api/runs/:run_id/runbooks/:runbook/steps", ({ params }) => {
    const id = String(params["run_id"]);
    if (!polls.has(id)) seedRuns();
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    if (id === "run_badsteps" && !failedStepsOnce.has(id)) {
      failedStepsOnce.add(id);
      return problem(422, "Unprocessable Content", "topup_not_credited is for visitor; this run is on topup");
    }
    const steps = stepsInRun(poll.request.value, ticks.get(id) ?? new Set(), stepResults.get(id) ?? new Map<string, StepInRun["result"]>());
    if (id !== "run_masked") return json(steps);
    return json(
      steps.map((step) =>
        step.id === "check-locks" ? { ...step, unfilled: [{ parameter: "customer_email", why: "masked in this run, so it is never filled in" }] } : step,
      ),
    );
  }),

  http.post("/api/runs/:run_id/runbooks/:runbook/steps/:step/run", async ({ params, request }) => {
    const id = String(params["run_id"]);
    const step = String(params["step"]);
    const body = (await request.json()) as { run_without_plan_check?: boolean; subject?: string };
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    if (piiKey() && (step === "revalidate" || step === "check-outbox") && !body.subject) {
      return problem(422, "Unprocessable Content", PII_KEY_REASON, [{ field: "subject", message: PII_KEY_REASON }]);
    }
    if (step === "revalidate") {
      const again = `run_${nextRun++}`;
      polls.set(again, { seen: 0, request: poll.request });
      return json({ id: step, run: again, result: null });
    }
    if (step !== "check-outbox") return problem(422, "Unprocessable Content", "this step is copy only: not reviewed yet");
    if (id === "run_scan") {
      return problem(422, "Unprocessable Content", "this step cannot run: Table Scan: the lookup would read all of outbox_events. Index the column it filters on, or look rows up by a key");
    }
    let warnings: string[] = [];
    if (id === "run_noplan") {
      if (!body.run_without_plan_check) {
        return problem(422, "Unprocessable Content", "plan checks could not run in prod (index use not checked: the connection was refused); to run without plan check, confirm it: that is recorded in the audit");
      }
      warnings = ["index use not checked: the connection was refused"];
    }
    const plain = outboxResult(warnings);
    // As the backend: a PII key, pasted for this step, is masked in what is kept and returned.
    const result = piiKey() && plain?.evidence ? { ...plain, evidence: { ...plain.evidence, parameters: { topup_id: "[masked]" } } } : plain;
    const results = stepResults.get(id) ?? new Map<string, StepInRun["result"]>();
    results.set(step, result);
    stepResults.set(id, results);
    return json({ id: step, result, run: null });
  }),

  http.post("/api/runs/:run_id/runbooks/:runbook/steps/:step/tick", async ({ params, request }) => {
    const id = String(params["run_id"]);
    const { done } = (await request.json()) as { done: boolean };
    const ticked = ticks.get(id) ?? new Set<string>();
    if (done) ticked.add(String(params["step"]));
    else ticked.delete(String(params["step"]));
    ticks.set(id, ticked);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/runs/:run_id/runbooks/:runbook/steps/:step/copy", ({ params }) => {
    const id = String(params["run_id"]);
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    const step = topUpNotCredited.steps.find((s) => s.id === params["step"]);
    if (step?.kind !== "write_query") return problem(422, "Unprocessable Content", "only a write query's copy is recorded here");
    const filled = step.sql.replaceAll(":topup_id", `'${poll.request.value}'`);
    return json({ text: `-- WRITE · UAT · run ${id} · needs ${step.requires}\n${filled}`, unfilled: [] });
  }),

  http.get("/api/runs/:run_id/healthy-comparison", ({ params }) => {
    const id = String(params["run_id"]);
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    // tp_500err stands for a comparison that cannot be read the first time, so the retry can be seen.
    if (poll.request.value === "tp_500err" && !failedOnce.has(id)) {
      failedOnce.add(id);
      return problem(500, "Internal Server Error", "The comparison could not be read.");
    }
    const entry = comparisons.get(id);
    if (entry) entry.seen += 1;
    return json(comparisonOf(id, poll.request.value));
  }),

  http.post("/api/runs/:run_id/healthy-comparison", async ({ params, request }) => {
    const id = String(params["run_id"]);
    const poll = polls.get(id);
    if (poll === undefined) return problem(404, "Not Found", "No such run.");
    const body = (await request.json()) as { narrow?: string | null; subject?: string | null; failing_subject?: string | null };
    if (piiKey() && !body.subject && !body.failing_subject) {
      return problem(422, "Unprocessable Content", PII_KEY_REASON, [{ field: "failing_subject", message: PII_KEY_REASON }]);
    }
    const stopped = body.subject ? undefined : stoppedBy(poll.request.application, "wallet_db");
    if (stopped) return problem(403, "Forbidden", stopped);
    if (body.subject === poll.request.value) return problem(422, "Unprocessable Content", "that is the subject of this run");
    if (body.subject && !/^tp_[0-9a-z]{6}$/.test(body.subject)) {
      return problem(422, "Unprocessable Content", `No identifier of the entity matches "${body.subject}".`);
    }
    const earlier = comparisons.get(id);
    comparisons.set(id, {
      seen: 0,
      healthy: outcomeOf(poll.request.value, body).healthy,
      attempt: {
        status: "running",
        narrow: body.narrow ?? null,
        pasted: Boolean(body.subject),
        replaced: earlier !== undefined,
        replaced_subject: earlier?.healthy ?? null,
        runs: [],
      },
    });
    return json(comparisonOf(id, poll.request.value), { status: 202 });
  }),

  http.post("/api/runs/:run_id/matches", async ({ params, request }) => {
    const id = String(params["run_id"]);
    const from = polls.get(id);
    if (from === undefined) return problem(404, "Not Found", "No such run.");
    const body = (await request.json()) as { subjects?: string[]; value?: string; picks?: number[] };
    const run = runFor(id, from.request, false);
    const known = (run.matches ?? []).map((m) => m.subject).sort();
    let subjects = body.subjects ?? [];
    if (piiKey()) {
      // Picked by number: the same lookup again, which here only the original value finds.
      if (body.value !== from.request.value) return problem(422, "Unprocessable Content", "the matches have changed since this run: start again");
      const picks = body.picks ?? [];
      if (picks.length === 0 || new Set(picks).size !== picks.length || picks.some((n) => n < 1 || n > known.length)) {
        return problem(422, "Unprocessable Content", `pick each number once, from 1 to ${known.length}`);
      }
      subjects = picks.map((n) => known[n - 1] ?? "");
    }
    if (run.status !== "ambiguous" || subjects.length === 0 || subjects.some((s) => !known.includes(s))) {
      return problem(422, "Unprocessable Content", "Pick subjects from the matches of an Ambiguous run.");
    }
    const runs = subjects.map((subject) => {
      const picked = `run_${nextRun++}`;
      polls.set(picked, { seen: 0, request: { ...from.request, identifier: visitorEntity.root_key, value: subject }, fromRun: id });
      return { id: picked, status: "running" };
    });
    return json({ runs }, { status: 202 });
  }),
];
