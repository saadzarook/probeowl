// Author: @SaadZarook
// Fails when the web mocks name things that examples/ does not have.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { applications, mockedRunbooks, mockedCatalogs, mockedConnectors, mockedEntities, mockedProbes, mockedReviews, mockedRuns, mockedSnapshots, UI_ONLY_APPLICATIONS } from "../mocks/fixtures";

const webDir = fileURLToPath(new URL("..", import.meta.url));
const examplesDir = join(webDir, "..", "examples");
const APPLICATIONS_FILE = "mocks/fixtures/applications.ts";

type Yaml = Record<string, unknown>;
interface RealEntity {
  title: string;
  root_key: string;
  identifiers: Map<string, string | undefined>;
  locations: Map<string, { title: string; using: string; parent?: string; exposes: string[] }>;
}
interface RealProbe {
  title: string;
  kind: string;
  entity: string;
  /** The expect and when texts of each step; an inspect probe's flat expect list is one step per rule. */
  steps: string[][];
}
interface RealTable {
  label: string;
  owner: string;
  description: string | null;
  retention: string | null;
  /** The marked columns exactly as catalog.yaml has them, with absent optional fields left out. */
  columns: Record<string, Yaml>;
}
interface RealApplication {
  name: string;
  title: string;
  owner: string;
  environments: { name: string; title: string; production: boolean }[];
  entities: Map<string, RealEntity>;
  probes: Map<string, RealProbe>;
  /** Catalog tables by "connector.table". */
  tables: Map<string, RealTable>;
  /** Connector name to its type (sqlserver, oracle, mariadb, rest). */
  connectors: Map<string, string>;
}

const problems: string[] = [];
/** Record one mismatch: the file, the field and what examples/ has. */
function mismatch(file: string, field: string, actual: unknown, expected: string): void {
  problems.push(`${file}: ${field} is ${JSON.stringify(actual)}, expected ${expected}`);
}
const oneOf = (names: Iterable<string>): string => `one of ${[...names].map((n) => JSON.stringify(n)).join(", ") || "(none)"}`;

function readYaml(path: string): Yaml {
  return parse(readFileSync(path, "utf8")) as Yaml;
}
function yamlFiles(dir: string): string[] {
  return readdirSync(dir).filter((f) => f.endsWith(".yaml")).map((f) => join(dir, f));
}
/** The title owl serve returns: the YAML's, else the name with spaces for underscores. */
function titleOf(spec: Yaml, name: string): string {
  if (typeof spec["title"] === "string" && spec["title"] !== "") return spec["title"];
  const spaced = name.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
const texts = (value: unknown): string[] => (value === undefined ? [] : [value].flat().map(String));

function loadEntity(doc: Yaml): RealEntity {
  const identifiers = new Map<string, string | undefined>();
  identifiers.set(String(doc["root_key"]), undefined);
  for (const [name, spec] of Object.entries((doc["identifiers"] ?? {}) as Record<string, Yaml>)) {
    identifiers.set(name, spec["via"] === undefined ? undefined : String(spec["via"]));
  }
  const locations: RealEntity["locations"] = new Map();
  for (const [name, spec] of Object.entries((doc["locations"] ?? {}) as Record<string, Yaml>)) {
    locations.set(name, { title: titleOf(spec, name), using: String(spec["using"]), exposes: texts(spec["exposes"]) });
    for (const [child, childSpec] of Object.entries((spec["each"] ?? {}) as Record<string, Yaml>)) {
      locations.set(child, { title: titleOf(childSpec, child), using: String(childSpec["using"]), parent: name, exposes: texts(childSpec["exposes"]) });
    }
  }
  return { title: titleOf(doc, String(doc["entity"])), root_key: String(doc["root_key"]), identifiers, locations };
}

function loadProbe(doc: Yaml): RealProbe {
  const steps = Array.isArray(doc["steps"])
    ? (doc["steps"] as Yaml[]).map((step) => [...texts(step["when"]), ...texts(step["expect"])])
    : texts(doc["expect"]).map((text) => [text]);
  return { title: titleOf(doc, String(doc["probe"])), kind: String(doc["kind"]), entity: String(doc["entity"]), steps };
}

function loadApplications(): Map<string, RealApplication> {
  const loaded = new Map<string, RealApplication>();
  for (const folder of readdirSync(examplesDir, { withFileTypes: true }).filter((d) => d.isDirectory())) {
    const dir = join(examplesDir, folder.name);
    const doc = readYaml(join(dir, "application.yaml"));
    const environments = Object.entries((doc["environments"] ?? {}) as Record<string, Yaml>).map(([name, spec]) => ({
      name,
      title: String(spec["title"]),
      production: spec["production"] === true,
    }));
    const entities = new Map<string, RealEntity>();
    const probes = new Map<string, RealProbe>();
    for (const path of yamlFiles(join(dir, "entities"))) {
      const entity = readYaml(path);
      entities.set(String(entity["entity"]), loadEntity(entity));
    }
    for (const path of yamlFiles(join(dir, "probes"))) {
      const probe = readYaml(path);
      probes.set(String(probe["probe"]), loadProbe(probe));
    }
    const tables = new Map<string, RealTable>();
    const catalogPath = join(dir, "catalog.yaml");
    const catalogDoc = existsSync(catalogPath) ? readYaml(catalogPath) : {};
    for (const table of (catalogDoc["tables"] ?? []) as Yaml[]) {
      tables.set(`${String(table["connector"])}.${String(table["table"])}`, {
        label: String(table["label"]),
        owner: String(table["owner"]),
        description: table["description"] === undefined ? null : String(table["description"]),
        retention: table["retention"] === undefined ? null : String(table["retention"]),
        columns: (table["columns"] ?? {}) as Record<string, Yaml>,
      });
    }
    const connectors = new Map<string, string>();
    for (const path of yamlFiles(join(dir, "connectors"))) {
      const connector = readYaml(path);
      connectors.set(String(connector["connector"]), String(connector["type"]));
    }
    const name = String(doc["application"]);
    loaded.set(name, { name, title: String(doc["title"]), owner: String(doc["owner"]), environments, entities, probes, tables, connectors });
  }
  return loaded;
}

function sameSet(file: string, field: string, actual: Iterable<string>, expected: Iterable<string>): void {
  const have = new Set(actual);
  const want = new Set(expected);
  const missing = [...want].filter((n) => !have.has(n));
  const extra = [...have].filter((n) => !want.has(n));
  if (missing.length > 0 || extra.length > 0) {
    mismatch(file, field, [...have], `${JSON.stringify([...want])} (missing ${JSON.stringify(missing)}, not in examples/ ${JSON.stringify(extra)})`);
  }
}

function checkApplications(real: Map<string, RealApplication>): void {
  const uiOnly = new Set(UI_ONLY_APPLICATIONS);
  for (const app of applications) {
    const where = `applications[${app.name}]`;
    const expected = real.get(app.name);
    if (expected === undefined) {
      if (!uiOnly.has(app.name)) mismatch(APPLICATIONS_FILE, `${where}.name`, app.name, `${oneOf(real.keys())}, or listed in UI_ONLY_APPLICATIONS`);
      continue;
    }
    if (app.title !== expected.title) mismatch(APPLICATIONS_FILE, `${where}.title`, app.title, JSON.stringify(expected.title));
    if (app.owner !== expected.owner) mismatch(APPLICATIONS_FILE, `${where}.owner`, app.owner, JSON.stringify(expected.owner));
    const actualEnvs = app.environments.map((e) => ({ name: e.name, title: e.title, production: e.production }));
    if (JSON.stringify(actualEnvs) !== JSON.stringify(expected.environments)) {
      mismatch(APPLICATIONS_FILE, `${where}.environments`, actualEnvs, JSON.stringify(expected.environments));
    }
  }
  const mocked = new Set(applications.map((a) => a.name));
  for (const name of real.keys()) {
    if (!mocked.has(name)) mismatch(APPLICATIONS_FILE, "applications", [...mocked], `an application named ${JSON.stringify(name)} (it is in examples/)`);
  }
  for (const name of uiOnly) {
    if (real.has(name)) mismatch(APPLICATIONS_FILE, "UI_ONLY_APPLICATIONS", name, "no application that is in examples/");
  }
}

function checkEntities(real: Map<string, RealApplication>): void {
  for (const { file, application, entity } of mockedEntities) {
    const app = real.get(application);
    if (app === undefined) {
      mismatch(file, "application", application, oneOf(real.keys()));
      continue;
    }
    const where = `entity ${entity.entity}`;
    const expected = app.entities.get(entity.entity);
    if (expected === undefined) {
      mismatch(file, `${where}.entity`, entity.entity, oneOf(app.entities.keys()));
      continue;
    }
    if (entity.root_key !== expected.root_key) mismatch(file, `${where}.root_key`, entity.root_key, JSON.stringify(expected.root_key));
    if (entity.title !== expected.title) mismatch(file, `${where}.title`, entity.title, JSON.stringify(expected.title));
    sameSet(file, `${where}.identifiers`, new Set([entity.root_key, ...Object.keys(entity.identifiers ?? {})]), expected.identifiers.keys());
    const names = Object.entries(entity.locations).flatMap(([name, location]) => [name, ...Object.keys(location.each ?? {})]);
    sameSet(file, `${where}.locations`, names, expected.locations.keys());
    for (const [name, location] of Object.entries(entity.locations)) {
      const titled = [[name, location.title], ...Object.entries(location.each ?? {}).map(([child, c]) => [child, c.title])];
      for (const [shown, title] of titled) {
        const want = expected.locations.get(shown ?? "")?.title;
        if (want !== undefined && title !== want) mismatch(file, `${where}.locations.${shown}.title`, title, JSON.stringify(want));
      }
      for (const child of Object.keys(location.each ?? {})) {
        const parent = expected.locations.get(child)?.parent;
        if (parent !== undefined && parent !== name) mismatch(file, `${where}.locations.${name}.each.${child}`, name, `parent ${JSON.stringify(parent)}`);
      }
    }
  }
}

function checkProbe(file: string, field: string, app: RealApplication, probe: string, kind: string, entity: string): RealProbe | undefined {
  const expected = app.probes.get(probe);
  if (expected === undefined) {
    mismatch(file, `${field}.probe`, probe, oneOf(app.probes.keys()));
    return undefined;
  }
  if (kind !== expected.kind) mismatch(file, `${field}.kind`, kind, JSON.stringify(expected.kind));
  if (entity !== expected.entity) mismatch(file, `${field}.entity`, entity, JSON.stringify(expected.entity));
  return expected;
}

function checkProbes(real: Map<string, RealApplication>): void {
  for (const { file, application, probes } of mockedProbes) {
    const app = real.get(application);
    if (app === undefined) continue;
    for (const probe of probes) {
      const expected = checkProbe(file, `probe ${probe.name}`, app, probe.name, probe.kind, probe.entity);
      if (expected && probe.title !== expected.title) mismatch(file, `probe ${probe.name}.title`, probe.title, JSON.stringify(expected.title));
    }
  }
}

function checkRuns(real: Map<string, RealApplication>): void {
  for (const { file, name, run } of mockedRuns) {
    const where = `${name}`;
    const app = real.get(run.application);
    if (app === undefined) {
      mismatch(file, `${where}.application`, run.application, oneOf(real.keys()));
      continue;
    }
    if (!app.environments.some((e) => e.name === run.environment)) {
      mismatch(file, `${where}.environment`, run.environment, oneOf(app.environments.map((e) => e.name)));
    }
    const probe = checkProbe(file, where, app, run.probe, run.kind, run.entity);
    if (probe && run.probe_title !== probe.title) mismatch(file, `${where}.probe_title`, run.probe_title, JSON.stringify(probe.title));
    const entity = app.entities.get(run.entity);
    if (entity === undefined) {
      mismatch(file, `${where}.entity`, run.entity, oneOf(app.entities.keys()));
      continue;
    }
    if (!entity.identifiers.has(run.identifier)) mismatch(file, `${where}.identifier`, run.identifier, oneOf(entity.identifiers.keys()));
    const via = entity.identifiers.get(run.identifier);
    if (run.via != null && run.via !== via) mismatch(file, `${where}.via`, run.via, JSON.stringify(via ?? null));

    const checkResult = (field: string, result: { location: string; title: string; parent?: string | null; evidence?: { connector: string } | null }): void => {
      const location = entity.locations.get(result.location);
      if (location === undefined) {
        mismatch(file, `${field}.location`, result.location, oneOf(entity.locations.keys()));
        return;
      }
      if (result.title !== location.title) mismatch(file, `${field}.title`, result.title, JSON.stringify(location.title));
      if ((result.parent ?? undefined) !== location.parent) mismatch(file, `${field}.parent`, result.parent ?? null, JSON.stringify(location.parent ?? null));
      if (result.evidence && result.evidence.connector !== location.using) {
        mismatch(file, `${field}.evidence.connector`, result.evidence.connector, JSON.stringify(location.using));
      }
    };
    (run.locations ?? []).forEach((result, i) => checkResult(`${where}.locations[${i}]`, result));
    if (run.resolution) {
      checkResult(`${where}.resolution`, run.resolution);
      const field = via?.split(".")[1];
      if (run.via !== `${run.resolution.location}.${field}`) mismatch(file, `${where}.resolution.location`, run.resolution.location, `the location of via ${JSON.stringify(via)}`);
      if (field !== undefined && !entity.locations.get(run.resolution.location)?.exposes.includes(field)) {
        mismatch(file, `${where}.via`, run.via, `a field the location exposes`);
      }
    }
    (run.expectations ?? []).forEach((e, i) => {
      const step = probe?.steps[e.step];
      if (probe !== undefined && (step === undefined || !step.includes(e.expectation))) {
        mismatch(file, `${where}.expectations[${i}].expectation`, e.expectation, step === undefined ? `a step of ${run.probe} (step ${e.step} does not exist)` : oneOf(step));
      }
    });
  }
}

/** A marked column as the API returns it: only the fields catalog.yaml sets. */
function markedColumn(column: Record<string, unknown>): Yaml {
  const { roles, indexed, values, links_to } = column;
  const links = links_to as Record<string, unknown> | null | undefined;
  return {
    roles,
    ...(indexed == null ? {} : { indexed }),
    ...(values === undefined ? {} : { values }),
    ...(links == null ? {} : { links_to: Object.fromEntries(Object.entries(links).filter(([, v]) => v != null)) }),
  };
}

/** The mocked runbook's steps, in order, each kind and id as examples/ has them. */
function checkRunbooks(): void {
  for (const { file, application, runbook } of mockedRunbooks) {
    const dir = readdirSync(examplesDir).find((name) => {
      const path = join(examplesDir, name, "application.yaml");
      return existsSync(path) && readYaml(path)["application"] === application;
    });
    const path = dir && join(examplesDir, dir, "runbooks", `${runbook.runbook}.yaml`);
    if (!path || !existsSync(path)) {
      mismatch(file, `runbook ${runbook.runbook}`, runbook.runbook, "no such file in examples/");
      continue;
    }
    const steps = (readYaml(path)["steps"] ?? []) as Yaml[];
    const wanted = steps.map((s) => `${String(s["id"])}:${String(s["kind"])}:${String(s["title"])}`);
    const actual = runbook.steps.map((s) => `${s.id}:${s.kind}:${s.title}`);
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      mismatch(file, `runbook ${runbook.runbook}.steps`, actual.join(", "), wanted.join(", "));
    }
  }
}

function checkCatalogs(real: Map<string, RealApplication>): void {
  for (const { file, application, catalog } of mockedCatalogs) {
    const app = real.get(application);
    if (app === undefined) {
      mismatch(file, "application", application, oneOf(real.keys()));
      continue;
    }
    sameSet(file, `catalog ${application}.tables`, catalog.tables.map((t) => `${t.connector}.${t.table}`), app.tables.keys());
    for (const table of catalog.tables) {
      const where = `catalog ${application} table ${table.connector}.${table.table}`;
      const expected = app.tables.get(`${table.connector}.${table.table}`);
      if (expected === undefined) continue;
      if (table.label !== expected.label) mismatch(file, `${where}.label`, table.label, JSON.stringify(expected.label));
      if (table.owner !== expected.owner) mismatch(file, `${where}.owner`, table.owner, JSON.stringify(expected.owner));
      if ((table.description ?? null) !== expected.description) mismatch(file, `${where}.description`, table.description ?? null, JSON.stringify(expected.description));
      if ((table.retention ?? null) !== expected.retention) mismatch(file, `${where}.retention`, table.retention ?? null, JSON.stringify(expected.retention));
      const actual = Object.fromEntries(Object.entries(table.columns).map(([name, column]) => [name, markedColumn(column)]));
      const wanted = Object.fromEntries(Object.entries(expected.columns).map(([name, column]) => [name, markedColumn(column)]));
      sameSet(file, `${where}.columns`, Object.keys(actual), Object.keys(wanted));
      for (const [name, column] of Object.entries(actual)) {
        if (wanted[name] !== undefined && JSON.stringify(column) !== JSON.stringify(wanted[name])) {
          mismatch(file, `${where}.columns.${name}`, column, JSON.stringify(wanted[name]));
        }
      }
    }
  }
}

function checkConnectors(real: Map<string, RealApplication>): void {
  for (const { file, application, environment, statuses } of mockedConnectors) {
    const app = real.get(application);
    if (app === undefined) {
      mismatch(file, "application", application, oneOf(real.keys()));
      continue;
    }
    const env = app.environments.find((e) => e.name === environment);
    if (env === undefined) mismatch(file, `connectors ${application}.environment`, environment, oneOf(app.environments.map((e) => e.name)));
    sameSet(file, `connectors ${application}/${environment}`, statuses.map((s) => s.connector), app.connectors.keys());
    for (const status of statuses) {
      const where = `connectors ${application}/${environment} ${status.connector}`;
      const type = app.connectors.get(status.connector);
      if (type !== undefined && status.type !== type) mismatch(file, `${where}.type`, status.type, JSON.stringify(type));
      if (status.environment !== environment) mismatch(file, `${where}.environment`, status.environment, JSON.stringify(environment));
      if (env !== undefined && status.production !== env.production) mismatch(file, `${where}.production`, status.production, JSON.stringify(env.production));
    }
  }
}

function checkReviews(real: Map<string, RealApplication>): void {
  for (const { file, application, tables } of mockedReviews) {
    const app = real.get(application);
    if (app === undefined) {
      mismatch(file, "application", application, oneOf(real.keys()));
      continue;
    }
    for (const table of tables) {
      const where = `review ${application} table ${table.connector}.${table.table}`;
      const confirmed = app.tables.get(`${table.connector}.${table.table}`);
      if (!app.connectors.has(table.connector)) mismatch(file, `${where}.connector`, table.connector, oneOf(app.connectors.keys()));
      if ((confirmed?.label ?? null) !== table.label) mismatch(file, `${where}.label`, table.label, JSON.stringify(confirmed?.label ?? null));
      for (const probe of table.used_by ?? []) {
        if (!app.probes.has(probe)) mismatch(file, `${where}.used_by`, probe, oneOf(app.probes.keys()));
      }
      for (const environment of [...table.imported_in, ...(table.drift ?? []).map((d) => d.environment)]) {
        if (!app.environments.some((e) => e.name === environment)) mismatch(file, `${where}.environment`, environment, oneOf(app.environments.map((e) => e.name)));
      }
    }
  }
}

function checkSnapshots(real: Map<string, RealApplication>): void {
  for (const { file, snapshot } of mockedSnapshots) {
    const where = `snapshot ${snapshot.application}/${snapshot.connector}/${snapshot.environment}`;
    const app = real.get(snapshot.application);
    if (app === undefined) {
      mismatch(file, `${where}.application`, snapshot.application, oneOf(real.keys()));
      continue;
    }
    if (!app.environments.some((e) => e.name === snapshot.environment)) mismatch(file, `${where}.environment`, snapshot.environment, oneOf(app.environments.map((e) => e.name)));
    const type = app.connectors.get(snapshot.connector);
    if (type === undefined || type === "rest") {
      mismatch(file, `${where}.connector`, snapshot.connector, oneOf([...app.connectors].filter(([, t]) => t !== "rest").map(([n]) => n)));
      continue;
    }
    // Every catalog table of the connector must be in the import, with its marked columns.
    for (const [key, table] of app.tables) {
      const [connector = "", name = ""] = key.split(".");
      if (connector !== snapshot.connector) continue;
      const imported = snapshot.tables.find((t) => t.table.toLowerCase() === name.toLowerCase());
      if (imported === undefined) {
        mismatch(file, `${where}.tables`, snapshot.tables.map((t) => t.table), `a table named ${JSON.stringify(name)} (it is in catalog.yaml)`);
        continue;
      }
      const have = imported.columns.map((c) => c.name.toLowerCase());
      for (const column of Object.keys(table.columns)) {
        if (!have.includes(column.toLowerCase())) mismatch(file, `${where}.${name}.columns`, have, `a column named ${JSON.stringify(column)} (it is marked in catalog.yaml)`);
      }
    }
  }
}

const real = loadApplications();
checkApplications(real);
checkEntities(real);
checkProbes(real);
checkRuns(real);
checkRunbooks();
checkCatalogs(real);
checkReviews(real);
checkConnectors(real);
checkSnapshots(real);

if (problems.length > 0) {
  console.error(`The web mocks disagree with examples/ in ${problems.length} place(s):\n${problems.map((p) => `  - ${p}`).join("\n")}`);
  process.exit(1);
}
console.log("The web mocks match examples/.");
