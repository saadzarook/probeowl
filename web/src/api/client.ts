// Author: @SaadZarook
import createClient from "openapi-fetch";
import { CONFIGURATION_HEADER, noteConfiguration } from "./configuration";
import type { components, paths } from "./schema";

export type ApplicationSummary = components["schemas"]["ApplicationSummary"];
export type EnvironmentSummary = components["schemas"]["EnvironmentSummary"];
export type Run = components["schemas"]["Run"];
export type RunSummary = components["schemas"]["RunSummary"];
export type RunList = components["schemas"]["RunList"];
export type StartRun = components["schemas"]["StartRun"];
export type Match = components["schemas"]["Match"];
export type Entity = components["schemas"]["Entity"];
export type ProbeSummary = components["schemas"]["ProbeSummary"];
export type Reason = components["schemas"]["Reason"];
export type LocationResult = components["schemas"]["LocationResult"];
export type ExpectationEvaluation = components["schemas"]["ExpectationEvaluation"];
export type SaveCheck = components["schemas"]["SaveCheck"];
export type SaveCheckReport = components["schemas"]["SaveCheckReport"];
export type DraftProbe = components["schemas"]["DraftProbe"];
export type DraftRun = components["schemas"]["DraftRun"];
export type Catalog = components["schemas"]["Catalog"];
export type CatalogTable = components["schemas"]["CatalogTable"];
export type Column = components["schemas"]["Column"];
export type SchemaSnapshot = components["schemas"]["SchemaSnapshot"];
export type TableSchema = components["schemas"]["TableSchema"];
export type TableLabel = components["schemas"]["TableLabel"];
export type ColumnRole = components["schemas"]["ColumnRole"];
export type LinkTarget = components["schemas"]["LinkTarget"];
export type CatalogReview = components["schemas"]["CatalogReview"];
export type TableReview = components["schemas"]["TableReview"];
export type LabelSuggestion = components["schemas"]["LabelSuggestion"];
export type ConnectorStatus = components["schemas"]["ConnectorStatus"];
export type BindingHealth = components["schemas"]["BindingHealth"];
export type CatalogChange = components["schemas"]["CatalogChange"];
export type ProbeChange = components["schemas"]["ProbeChange"];
export type YamlFile = components["schemas"]["YamlFile"];
export type PluginManifest = components["schemas"]["PluginManifest"];
export type KillSwitch = components["schemas"]["KillSwitch"];
export type SetKillSwitch = components["schemas"]["SetKillSwitch"];
export type KillTarget = Pick<SetKillSwitch, "scope" | "application" | "connector">;
export type SaveProbe = components["schemas"]["SaveProbe"];
export type FieldError = components["schemas"]["FieldError"];

/** The only way the web app talks to the backend; typed from contracts/openapi.yaml. */
export const api = createClient<paths>({ baseUrl: "", headers: { "X-ProbeOwl-Request": "1" } });

// Every answer says which configuration it came from, so a reload elsewhere reaches open screens.
api.use({
  onResponse({ response }) {
    noteConfiguration(response.headers.get(CONFIGURATION_HEADER));
    return undefined;
  },
});

/** A failed request: its whole message, its detail alone, and what each field got wrong. */
export class ProblemError extends Error {
  constructor(
    message: string,
    readonly detail: string,
    readonly fields: Readonly<Record<string, string>>,
  ) {
    super(message);
  }
}

/** A failed request's problem details as an error a person can read. */
export function problemError(problem: components["schemas"]["Problem"] | undefined, fallback: string): ProblemError {
  const message = problem?.detail ?? problem?.title ?? fallback;
  const errors = problem?.errors ?? [];
  const fields = errors.map((e) => `${e.field} (${e.message})`);
  const text = fields.length > 0 ? `${message} ${fields.join("; ")}` : message;
  const byField = Object.fromEntries(errors.map((e) => [e.field, e.message]));
  if (problem?.status === 403) {
    return new ProblemError(
      `${text} If owl serve doesn't know this web app's address, start it with --web-origin ${window.location.origin}.`,
      message,
      byField,
    );
  }
  return new ProblemError(text, message, byField);
}

/** A failed request's message split for a form: what one field got wrong, shown at the field, and the rest. */
export function splitError(error: Error | null | undefined, field: string): { field?: string; rest?: string } {
  if (!error) return {};
  if (error instanceof ProblemError && error.fields[field] !== undefined) {
    return { field: error.fields[field], rest: error.detail };
  }
  return { rest: error.message };
}

/** List the applications, each with its environments. */
export async function fetchApplications(): Promise<ApplicationSummary[]> {
  const { data, error } = await api.GET("/api/applications");
  if (data) return data;
  throw problemError(error, "Applications could not be loaded.");
}

/** Start a run; resolves with the run id to poll. */
export async function startRun(body: StartRun): Promise<string> {
  const { data, error } = await api.POST("/api/runs", { body });
  if (data) return data.id;
  throw problemError(error, "The run could not be started.");
}

export type RunbookView = components["schemas"]["RunbookView"];
export type StepInRun = components["schemas"]["StepInRun"];
export type StepRan = components["schemas"]["StepRan"];
export type CopiedWriteQuery = components["schemas"]["CopiedWriteQuery"];
export interface RunbookFilters {
  q?: string;
  entity?: string;
  table?: string;
  identifier?: string;
}

export type SuggestedRunbook = components["schemas"]["SuggestedRunbook"];

/** The runbooks whose applies_to matches a run's outcomes, closest first. */
export async function fetchSuggestedRunbooks(run: string): Promise<SuggestedRunbook[]> {
  const { data, error } = await api.GET("/api/runs/{run_id}/runbooks", {
    params: { path: { run_id: run } },
  });
  if (data) return data;
  throw problemError(error, "The suggested runbooks could not be loaded.");
}

/** An application's runbooks matching every filter given. */
export async function fetchRunbooks(application: string, filters: RunbookFilters): Promise<RunbookView[]> {
  const { data, error } = await api.GET("/api/applications/{application}/runbooks", {
    params: { path: { application }, query: filters },
  });
  if (data) return data;
  throw problemError(error, "The runbooks could not be loaded.");
}

/** A runbook's steps with one run's values, ticks and latest results. */
export async function fetchStepsInRun(run: string, runbook: string): Promise<StepInRun[]> {
  const { data, error } = await api.GET("/api/runs/{run_id}/runbooks/{runbook}/steps", {
    params: { path: { run_id: run, runbook } },
  });
  if (data) return data;
  throw problemError(error, "This run's values could not be loaded.");
}

/** Run one step by its ID. The server has its SQL and its values, and decides whether it may run. */
export async function runStep(run: string, runbook: string, step: string, acknowledged: boolean): Promise<StepRan> {
  const { data, error } = await api.POST("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/run", {
    params: { path: { run_id: run, runbook, step } },
    body: { run_without_plan_check: acknowledged },
  });
  if (data) return data;
  throw problemError(error, "The step could not be run.");
}

/** Save a manual step's tick with the run. */
export async function tickStep(run: string, runbook: string, step: string, done: boolean): Promise<void> {
  const { response, error } = await api.POST("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/tick", {
    params: { path: { run_id: run, runbook, step } },
    body: { done },
  });
  if (!response.ok) throw problemError(error, "The tick could not be saved.");
}

/** A write query's text under its header. The server records the copy first; it never runs it. */
export async function copyWrite(application: string, runbook: string, step: string, run: string | undefined): Promise<CopiedWriteQuery> {
  const { data, error } = run
    ? await api.POST("/api/runs/{run_id}/runbooks/{runbook}/steps/{step}/copy", { params: { path: { run_id: run, runbook, step } } })
    : await api.POST("/api/applications/{application}/runbooks/{runbook}/steps/{step}/copy", { params: { path: { application, runbook, step } } });
  if (data) return data;
  throw problemError(error, "The copy could not be recorded.");
}

export type HealthyComparison = components["schemas"]["HealthyComparison"];
export type HealthyAttempt = components["schemas"]["HealthyAttempt"];
export type ComparedField = components["schemas"]["ComparedField"];
export type StartHealthyComparison = components["schemas"]["StartHealthyComparison"];

/** A run's latest comparison with a known-good subject, or what could start one. */
export async function fetchHealthyComparison(runId: string): Promise<HealthyComparison> {
  const { data, error } = await api.GET("/api/runs/{run_id}/healthy-comparison", {
    params: { path: { run_id: runId } },
  });
  if (data) return data;
  throw problemError(error, "The comparison could not be loaded.");
}

/** Start comparing a finished run with a known-good subject, replacing its last comparison. */
export async function startHealthyComparison(runId: string, body: StartHealthyComparison): Promise<HealthyComparison> {
  const { data, error } = await api.POST("/api/runs/{run_id}/healthy-comparison", {
    params: { path: { run_id: runId } },
    body,
  });
  if (data) return data;
  throw problemError(error, "The comparison could not be started.");
}

/** Start one run per picked subject of an Ambiguous run; resolves with the run ids to poll. */
export async function pickMatches(runId: string, subjects: string[]): Promise<string[]> {
  const { data, error } = await api.POST("/api/runs/{run_id}/matches", {
    params: { path: { run_id: runId } },
    body: { subjects },
  });
  if (data) return data.runs.map((r) => r.id);
  throw problemError(error, "The matches could not be inspected.");
}

/** A page of runs in one application and environment, newest first; before is the last page's next. */
export async function fetchRuns(application: string, environment: string, before: string | undefined): Promise<RunList> {
  const { data, error } = await api.GET("/api/runs", { params: { query: { application, environment, before } } });
  if (data) return data;
  throw problemError(error, "The runs could not be loaded.");
}

/** Read a run as it progresses. */
export async function fetchRun(id: string): Promise<Run> {
  const { data, error } = await api.GET("/api/runs/{run_id}", { params: { path: { run_id: id } } });
  if (data) return data;
  throw problemError(error, "The run could not be loaded.");
}

/** List an application's entities. */
export async function fetchEntities(application: string): Promise<Entity[]> {
  const { data, error } = await api.GET("/api/applications/{application}/entities", {
    params: { path: { application } },
  });
  if (data) return data;
  throw problemError(error, "Entities could not be loaded.");
}

/** One entity with its identifiers and locations. */
export async function fetchProbeYaml(application: string, probe: string): Promise<YamlFile> {
  const { data, error } = await api.GET("/api/applications/{application}/probes/{probe}/yaml", {
    params: { path: { application, probe } },
  });
  if (data) return data;
  throw problemError(error, "The probe's file could not be opened.");
}

/** An entity's file as it is on disk, comments included. */
export async function fetchEntityYaml(application: string, entity: string): Promise<YamlFile> {
  const { data, error } = await api.GET("/api/applications/{application}/entities/{entity}/yaml", {
    params: { path: { application, entity } },
  });
  if (data) return data;
  throw problemError(error, "The entity's file could not be opened.");
}

/** One entity of an application. */
export async function fetchEntity(application: string, entity: string): Promise<Entity> {
  const { data, error } = await api.GET("/api/applications/{application}/entities/{entity}", {
    params: { path: { application, entity } },
  });
  if (data) return data;
  throw problemError(error, "The entity could not be loaded.");
}

/** List an application's probes, for one entity when given. */
export async function fetchProbes(application: string, entity?: string): Promise<ProbeSummary[]> {
  const { data, error } = await api.GET("/api/applications/{application}/probes", {
    params: { path: { application }, query: { entity } },
  });
  if (data) return data;
  throw problemError(error, "Probes could not be loaded.");
}

/** An application's confirmed catalog; null when it has none (404). */
export async function fetchCatalog(application: string): Promise<Catalog | null> {
  const { data, error, response } = await api.GET("/api/applications/{application}/catalog", {
    params: { path: { application } },
  });
  if (data) return data;
  if (response.status === 404) return null;
  throw problemError(error, "The catalog could not be loaded.");
}

/** What a connector's schema lookup found: the latest import, or why there is none yet. */
export type SchemaResult = { snapshot: SchemaSnapshot; notice?: undefined } | { snapshot?: undefined; notice: string };

/** The latest imported schema of one connector in one environment; a 404 means it was never imported. */
export async function fetchSchema(application: string, connector: string, environment: string): Promise<SchemaResult> {
  const { data, error, response } = await api.GET("/api/applications/{application}/catalog/schema", {
    params: { path: { application }, query: { connector, environment } },
  });
  if (data) return { snapshot: data };
  if (response.status === 404) return { notice: error?.detail ?? "This connector has not been imported in this environment." };
  throw problemError(error, "The imported schema could not be loaded.");
}

/** Import a SQL connector's schema (definitions only, never data); resolves with the new snapshot. */
export async function importSchema(application: string, connector: string, environment: string): Promise<SchemaSnapshot> {
  const { data, error } = await api.POST("/api/applications/{application}/catalog/imports", {
    params: { path: { application } },
    body: { connector, environment },
  });
  if (data) return data;
  throw problemError(error, "The schema could not be imported.");
}

/** The review of an application's tables: suggestions, drift, usage; null when it has no catalog (404). */
export async function fetchCatalogReview(application: string): Promise<CatalogReview | null> {
  const { data, error, response } = await api.GET("/api/applications/{application}/catalog/review", {
    params: { path: { application } },
  });
  if (data) return data;
  if (response.status === 404) return null;
  throw problemError(error, "The catalog review could not be loaded.");
}

/** A failed confirm; `status` tells a conflict (409) from an invalid entry (422). */
export class ConfirmError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Preview a table's catalog entry (no base: nothing is written), or write it with the preview's base. */
export async function confirmTable(application: string, entry: CatalogTable, base?: string): Promise<CatalogChange> {
  const { data, error, response } = await api.PUT("/api/applications/{application}/catalog/tables/{connector}/{table}", {
    params: { path: { application, connector: entry.connector, table: entry.table } },
    body: base === undefined ? { entry } : { entry, base },
  });
  if (data) return data;
  throw new ConfirmError(problemError(error, "The table could not be confirmed.").message, response.status);
}

/** The save checks of a draft probe; nothing is written and no data is read. */
export async function checkDraft(application: string, body: DraftProbe): Promise<SaveCheckReport> {
  const { data, error } = await api.POST("/api/applications/{application}/probes/checks", {
    params: { path: { application } },
    body,
  });
  if (data) return data;
  throw problemError(error, "The save checks could not run.");
}

/** The error field that asks for the person's acknowledgement before saving without a plan check. */
export const PLAN_CHECK_FIELD = "save_without_plan_check";

/** A failed save; `status` tells a failed check (422) from a changed file (409), `errors` lists each failure. */
export class SaveError extends Error {
  constructor(message: string, readonly status: number, readonly errors: FieldError[]) {
    super(message);
  }

  /** True when plan checks could not run in Production and the person must acknowledge saving anyway. */
  get needsAcknowledgement(): boolean {
    return this.status === 422 && this.errors.some((e) => e.field === PLAN_CHECK_FIELD);
  }
}

/** Preview a probe save (no base: nothing is written), or write it with the preview's base. */
export async function saveProbe(application: string, probe: string, body: SaveProbe): Promise<ProbeChange> {
  const { data, error, response } = await api.PUT("/api/applications/{application}/probes/{probe}", {
    params: { path: { application, probe } },
    body,
  });
  if (data) return data;
  const fallback =
    response.status === 501
      ? "owl serve was not started with a config folder, so probes cannot be saved."
      : response.status === 404
        ? "The application, environment or probe was not found."
        : "The probe could not be saved.";
  throw new SaveError(error?.detail ?? error?.title ?? fallback, response.status, error?.errors ?? []);
}

export type SaveEntity = components["schemas"]["SaveEntity"];
export type EntityChange = components["schemas"]["EntityChange"];

/** Preview a new entity's save (no base: nothing is written), or write it with the preview's base. */
export async function saveEntity(application: string, entity: string, body: SaveEntity): Promise<EntityChange> {
  const { data, error, response } = await api.PUT("/api/applications/{application}/entities/{entity}", {
    params: { path: { application, entity } },
    body,
  });
  if (data) return data;
  const fallback =
    response.status === 501 ? "owl serve was not started with a config folder, so entities cannot be saved." : "The entity could not be saved.";
  throw new SaveError(error?.detail ?? error?.title ?? fallback, response.status, error?.errors ?? []);
}

/** A draft test run that failed a save check (422); `failures` has one entry per failing check. */
export class DraftRefusedError extends Error {
  constructor(message: string, readonly failures: { field: string; message: string }[]) {
    super(message);
  }
}

/** Run a draft probe without saving it; resolves with the run id to poll. */
export async function startDraftRun(application: string, body: DraftRun): Promise<string> {
  const { data, error, response } = await api.POST("/api/applications/{application}/probes/test-runs", {
    params: { path: { application } },
    body,
  });
  if (data) return data.id;
  if (response.status === 422 && (error?.errors ?? []).length > 0) {
    throw new DraftRefusedError(error?.detail ?? "The draft failed its save checks.", error?.errors ?? []);
  }
  throw problemError(error, "The test run could not be started.");
}

/** Every connector of an application in one environment, read live; the backend caches each reading for 60 s. */
/** The plugins that are on. */
export type Actor = components["schemas"]["Actor"];

/** Who is acting: the person running owl serve, as runs and the audit name them. */
export async function fetchMe(): Promise<Actor> {
  const { data, error } = await api.GET("/api/me");
  if (data) return data;
  throw problemError(error, "Who is signed in could not be read.");
}

/** The plugins that are on. */
export async function fetchPlugins(): Promise<PluginManifest[]> {
  const { data, error } = await api.GET("/api/plugins");
  if (data) return data;
  throw problemError(error, "The plugins could not be loaded.");
}

/** The kill switches that are on. A 503 means nobody can tell, which runs treat as stopped. */
export async function fetchKillSwitches(): Promise<KillSwitch[]> {
  const { data, error } = await api.GET("/api/kill-switches");
  if (data) return data;
  throw problemError(error, "The kill switches could not be read.");
}

/** Turn a kill switch on. */
export async function engageKillSwitch(body: SetKillSwitch): Promise<KillSwitch> {
  const { data, error } = await api.POST("/api/kill-switches", { body });
  if (data) return data;
  throw problemError(error, "The kill switch could not be turned on.");
}

/** Turn a kill switch off. */
export async function releaseKillSwitch(body: SetKillSwitch): Promise<KillSwitch> {
  const { data, error } = await api.POST("/api/kill-switches/release", { body });
  if (data) return data;
  throw problemError(error, "The kill switch could not be turned off.");
}

export type ConfigReload = components["schemas"]["ConfigReload"];

/** Make owl serve load its config folder again. 409 while a run is running, 422 if it would not start. */
export async function reloadConfiguration(): Promise<ConfigReload> {
  const { data, error } = await api.POST("/api/config/reload");
  if (data) return data;
  throw problemError(error, "The configuration could not be reloaded.");
}

export type ConnectionTest = components["schemas"]["ConnectionTest"];
export type SetupState = components["schemas"]["SetupState"];
export type SaveApplication = components["schemas"]["SaveApplication"];
export type SaveConnector = components["schemas"]["SaveConnector"];
export type DraftConnection = components["schemas"]["DraftConnection"];
export type SaveSecret = components["schemas"]["SaveSecret"];
export type SecretState = components["schemas"]["SecretState"];

/** Connect once to one binding, now. A kill switch refuses it with a 403. */
export async function testConnection(application: string, connector: string, environment: string): Promise<ConnectionTest> {
  const { data, error } = await api.POST("/api/applications/{application}/connectors/{connector}/test", {
    params: { path: { application, connector }, query: { environment } },
  });
  if (data) return data;
  throw problemError(error, "The connection could not be tested.");
}

export async function fetchConnectors(application: string, environment: string): Promise<ConnectorStatus[]> {
  const { data, error } = await api.GET("/api/applications/{application}/connectors", {
    params: { path: { application }, query: { environment } },
  });
  if (data) return data;
  throw problemError(error, "The connectors could not be loaded.");
}
