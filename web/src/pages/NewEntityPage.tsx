// Author: @SaadZarook
import { Button, buttonClass, EntityGraph, EntityOutline, Field, FormGrid, Input, Select, useMediaQuery } from "../ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { DraftRefusedError, saveEntity, SaveError, type EntityChange, type EnvironmentSummary, type Reason, type TableReview } from "../api/client";
import { notImported, useCatalog, useCatalogReview, useSchemas } from "../api/useCatalog";
import { useConnectors } from "../api/useConnectors";
import { useEntities } from "../api/useEntities";
import { useEntityCheck } from "../api/useEntityCheck";
import { useSampleRuns, type SampleRequest } from "../api/useSampleRuns";
import { ChangePreview } from "../components/ChangePreview";
import { EmptyState } from "../components/EmptyState";
import { EntityIdentifiers } from "../components/EntityIdentifiers";
import { EntityLocations } from "../components/EntityLocations";
import { SaveChecksPanel } from "../components/SaveChecksPanel";
import { draftProblems, entityTitle, entityYaml, type DraftIdentifier, type DraftLocation, type EntityDraft } from "../entityDraft";
import { defaultRootKey, identifierCandidates, importedTables, pretickedIdentifiers, rootLocation, tableReference } from "../followKeys";
import { failuresByLocation } from "../locationChecks";
import { REASON_LABELS, REFERENCE_PLACEHOLDER } from "../reasons";
import { boxOutcome, graphOf, lastCheck, sampleProbeYaml } from "../sampleRun";
import { useSelection } from "../selection";

// New entity is one screen, the entity graph (#163): the root table, a sample (one real subject's
// ID), and a box per location showing its outcome on that sample as the entity is built. Outside
// Production the sample is looked up again a second after each change; in Production only on Check
// sample, with the reason and reference asked once per session.

/** Fields the draft uses elsewhere, by location: the key graph's and the identifiers'. They stay selected. */
function fieldsInUse(draft: EntityDraft): Map<string, Set<string>> {
  const used = new Map<string, Set<string>>();
  const mark = (location: string, field: string) => used.set(location, (used.get(location) ?? new Set()).add(field));
  for (const l of draft.locations) if (l.keyFrom) mark(l.keyFrom.location, l.keyFrom.field);
  for (const i of draft.identifiers) if (i.via) mark(i.via.location, i.via.field);
  const root = draft.locations[0];
  if (root) mark(root.name, draft.rootKey);
  return used;
}

/** The application's other environments where some table was imported, in the application's order. */
function importedElsewhere(environments: EnvironmentSummary[], current: string, tables: TableReview[]): EnvironmentSummary[] {
  const imported = new Set(tables.flatMap((t) => t.imported_in));
  return environments.filter((e) => e.name !== current && imported.has(e.name));
}

/** An entity id from a table's name: its last part, lower case, as an id may be written. */
function idFromTable(table: string): string {
  const name = table.split(".").at(-1) ?? table;
  return name.toLowerCase().replace(/[^a-z0-9_]/g, "_");
}

/** Production's reason and reference, kept for the browser session so they are asked once. */
interface Investigation {
  reason: Reason | "";
  reference: string;
}

const investigationKey = (application: string) => `probeowl:graph-investigation:${application}`;

function storedInvestigation(application: string): Investigation {
  try {
    const raw = window.sessionStorage.getItem(investigationKey(application));
    const parsed = raw ? (JSON.parse(raw) as Partial<Investigation>) : {};
    return { reason: parsed.reason ?? "", reference: parsed.reference ?? "" };
  } catch {
    // Storage blocked or unreadable: ask again, which is the safe side.
    return { reason: "", reference: "" };
  }
}

function storeInvestigation(application: string, investigation: Investigation): void {
  try {
    window.sessionStorage.setItem(investigationKey(application), JSON.stringify(investigation));
  } catch {
    // Not kept: it is asked again next time, nothing else changes.
  }
}

/** New entity: the entity graph, built against a sample. */
export function NewEntityPage() {
  const { application, environment, selectEnvironment } = useSelection();
  const connectors = useConnectors(application?.name, environment?.name);
  // An entity that exists is opened from its probes, so its id is refused here, as it is typed.
  const existing = useEntities(application?.name);
  const catalog = useCatalog(application?.name);
  const review = useCatalogReview(application?.name);
  const sql = (connectors.data ?? []).filter((c) => c.type !== "rest").map((c) => c.connector);
  const schemas = useSchemas(application?.name, sql, environment?.name, new Set(sql));
  const snapshots = [...schemas.values()].flatMap((r) => (r.snapshot ? [r.snapshot] : []));
  const notices = sql.filter((c) => schemas.get(c)?.notice !== undefined).map((c) => notImported(application?.name ?? "", c, environment?.name ?? ""));
  const tables = importedTables(snapshots);
  const catalogTables = catalog.data?.tables ?? [];
  const rowCeilings = new Map((connectors.data ?? []).flatMap((c) => (c.row_ceiling ? [[c.connector, c.row_ceiling] as const] : [])));
  const wide = useMediaQuery("(min-width: 1024px)");

  const [draft, setDraft] = useState<EntityDraft>({ entity: "", title: "", owner: "", rootKey: "", locations: [], identifiers: [] });
  const [root, setRoot] = useState("");
  const [sample, setSample] = useState("");
  const [selected, setSelected] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [preview, setPreview] = useState<{ change: EntityChange; yaml: string }>();
  const [investigation, setInvestigation] = useState<Investigation>(() => storedInvestigation(application?.name ?? ""));
  const client = useQueryClient();

  // The graph opens in the application's first environment outside Production, once.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || application === undefined || environment === undefined) return;
    opened.current = true;
    if (!environment.production) return;
    const outside = application.environments.find((e) => !e.production);
    if (outside) selectEnvironment(outside.name);
  }, [application, environment, selectEnvironment]);

  const owner = draft.owner || (application?.owner ?? "");
  const current: EntityDraft = { ...draft, owner };
  const yaml = entityYaml(current);
  const problems = draftProblems(current);
  const taken = (existing.data ?? []).some((e) => e.entity === current.entity);
  const production = environment?.production === true;

  const save = useMutation({
    mutationFn: (base: string | undefined) =>
      saveEntity(application?.name ?? "", current.entity, {
        entity: yaml,
        environment: environment?.name ?? "",
        save_without_plan_check: base !== undefined && acknowledged,
        ...(base ? { base } : {}),
      }),
    onSuccess: (change) => {
      if (change.written) void client.invalidateQueries({ queryKey: ["entities", application?.name] });
      else setPreview({ change, yaml });
    },
  });
  const done = save.isSuccess && save.data.written;

  // Every change is checked as it would be saved, so a failing location shows where it is.
  const checkable = !done && current.locations.length > 0 && problems.length === 0;
  const check = useEntityCheck(application?.name ?? "", current.entity, yaml, environment?.name ?? "", catalog.dataUpdatedAt, application !== undefined && environment !== undefined && checkable);

  // The sample's lookup: a draft test run of the generated Inspect probe. Outside Production its
  // reason is a proactive check, noted as the graph's; in Production the person gives both.
  const investigated = production ? investigation.reason !== "" && investigation.reference.trim() !== "" : true;
  const probe = sampleProbeYaml(current);
  const request = ((): SampleRequest | undefined => {
    if (!checkable || taken || sample.trim() === "" || environment === undefined || !investigated) return undefined;
    const reason: Reason = production && investigation.reason !== "" ? investigation.reason : "proactive_check";
    const reference = production ? investigation.reference.trim() : undefined;
    return {
      // The catalog too: a lookup refused for a missing catalog link runs again once it is confirmed.
      key: JSON.stringify([yaml, probe, sample.trim(), environment.name, catalog.dataUpdatedAt]),
      body: {
        probe,
        entity: yaml,
        environment: environment.name,
        value: sample.trim(),
        investigation: { reason, ...(reference ? { reference } : {}), notes: "Entity graph: looking up the sample" },
      },
    };
  })();
  const lookups = useSampleRuns(application?.name ?? "", request, !production);

  if (application === undefined || environment === undefined) {
    return <EmptyState level={1} title="Choose an application" message="An entity belongs to one application." />;
  }

  const update = (change: Partial<EntityDraft>) => {
    setDraft({ ...current, ...change });
    setPreview(undefined);
  };
  const chooseRoot = (key: string) => {
    setRoot(key);
    const table = tables.find((t) => `${t.connector}.${t.table}` === key);
    if (!table) return update({ locations: [], identifiers: [] });
    // The id comes from the table, unless one was typed.
    const entity = current.entity || idFromTable(table.table);
    const rootKey = defaultRootKey(entity, table);
    const location = rootLocation(table, tables, catalogTables, rootKey);
    setSelected(location.name);
    update({ entity, rootKey, locations: [location], identifiers: [{ name: rootKey, unique: true, pii: false, pattern: "" }] });
  };
  const renameRootKey = (rootKey: string) => {
    // Every location keyed by the root key exposes its key column under the root key's name.
    const locations = current.locations.map((l) =>
      l.keyFrom ? l : { ...l, columns: l.columns.map((c) => (c.column === l.keyColumn ? { ...c, field: rootKey } : c)) },
    );
    const identifiers = current.identifiers.map((i) => (i.via === undefined ? { ...i, name: rootKey } : i));
    update({ rootKey, locations, identifiers });
  };
  const setLocations = (locations: DraftLocation[]) => {
    // An identifier on a location that is gone, or on a column no longer selected, goes too.
    const chosen = (i: DraftIdentifier) => locations.some((l) => l.name === i.via?.location && l.columns.some((c) => c.selected && c.field === i.via?.field));
    const kept = current.identifiers.filter((i) => i.via === undefined || chosen(i));
    // A location just added brings its unique, non-PII columns as identifiers, ticked, and is selected.
    const added = locations.filter((l) => !current.locations.some((c) => c.name === l.name));
    if (added[0]) setSelected(added[0].name);
    else if (!locations.some((l) => l.name === selected)) setSelected(locations[0]?.name ?? "");
    update({ locations, identifiers: [...kept, ...pretickedIdentifiers(added, tables, current.rootKey, kept)] });
  };
  const investigate = (change: Partial<Investigation>) => {
    const next = { ...investigation, ...change };
    setInvestigation(next);
    storeInvestigation(application.name, next);
  };

  const stale = preview !== undefined && preview.yaml !== yaml;
  const saveError = save.error instanceof SaveError ? save.error : undefined;
  const failures = failuresByLocation(check.data?.failures ?? [], current.locations.map((l) => l.name));
  const checks = {
    checking: checkable && (check.isFetching || check.data === undefined),
    byLocation: failures.byLocation,
    general: failures.general,
    ...(check.isError ? { error: check.error.message } : {}),
    onRetry: () => void check.refetch(),
  };
  const graph = graphOf(current, lookups.state);
  const focus = current.locations.find((l) => l.name === selected) ?? current.locations[0];
  const focusOutcome = focus ? boxOutcome(focus.name, lookups.state) : undefined;
  const refusal = lookups.error instanceof DraftRefusedError ? lookups.error : undefined;

  let body;
  if (connectors.isPending || catalog.isPending) {
    body = <p aria-busy="true">Loading the imported tables…</p>;
  } else if (connectors.isError) {
    body = (
      <div className="error-state" role="alert">
        <p>{connectors.error.message}</p>
        <Button type="button" onClick={() => void connectors.refetch()}>
          Retry
        </Button>
      </div>
    );
  } else if (done) {
    body = (
      <section className="card" role="status" aria-label="Entity created">
        <h2>{current.entity} was created</h2>
        <p>
          Written to <code>{save.data.path}</code>. Now write its first probe.
        </p>
        {/* Validate, as the guide's first application goes on to (#101); the kind can be changed there. */}
        <RouterLink className={buttonClass()} to={`/probes?new=validate&entity=${encodeURIComponent(current.entity)}`}>
          Write a probe for {current.entity}
        </RouterLink>
      </section>
    );
  } else if (tables.length === 0) {
    const elsewhere = importedElsewhere(application.environments, environment.name, review.data?.tables ?? []);
    body = (
      <EmptyState title={`No tables imported in ${environment.title}`} message="The entity graph starts from an imported schema. Import a SQL connector's schema first.">
        {elsewhere.length > 0 ? (
          <div className="actions" role="group" aria-label="Imported elsewhere">
            <p className="hint">Imported in {elsewhere.map((e) => e.title).join(", ")}. Build the entity there:</p>
            {elsewhere.map((e) => (
              <Button key={e.name} onClick={() => selectEnvironment(e.name)}>
                Use {e.title}
              </Button>
            ))}
          </div>
        ) : (
          notices.map((n) => (
            <p key={n} className="hint">
              {n}
            </p>
          ))
        )}
        <RouterLink to="/tables">Tables: Import from replica</RouterLink>
      </EmptyState>
    );
  } else {
    const rootTable = current.locations[0];
    body = (
      <>
        <section className="card" aria-labelledby="start-title">
          <h2 id="start-title">Root table and sample</h2>
          <FormGrid>
            <Field id="root-table" label="Root table" help="The table one subject is a row of. Lookups start from its key.">
              <Select value={root} onChange={(e) => chooseRoot(e.target.value)}>
                <option value="">Choose…</option>
                {tables.map((t) => (
                  <option key={`${t.connector}.${t.table}`} value={`${t.connector}.${t.table}`}>
                    {t.connector} · {tableReference(t, tables)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field id="entity-id" label="Entity id" error={taken ? `${current.entity} already exists` : undefined}>
              <Input value={current.entity} placeholder="from the root table" onChange={(e) => update({ entity: e.target.value.trim() })} />
            </Field>
            <Field
              id="sample"
              label="Sample"
              help={rootTable ? `One real subject's ${current.rootKey}, or any identifier. Every box shows its outcome on it.` : "One real subject's ID, once the root table is chosen."}
            >
              <Input value={sample} placeholder={rootTable ? `a ${current.rootKey}` : ""} disabled={!rootTable} onChange={(e) => setSample(e.target.value)} />
            </Field>
            {rootTable && (
              <Field id="root-key" label={`Root key, the name ${rootTable.keyColumn} goes by`}>
                <Input value={current.rootKey} onChange={(e) => renameRootKey(e.target.value.trim())} />
              </Field>
            )}
          </FormGrid>
          {taken && (
            // The field says so as an alert; this says what to do about it.
            <p className="hint" id="entity-taken">
              <RouterLink to={`/probes?new=inspect&entity=${encodeURIComponent(current.entity)}`}>Open {current.entity}</RouterLink>, or choose another name.
            </p>
          )}
          {production && (
            <div role="group" aria-label="Production lookups">
              <p className="hint">In Production the sample is looked up only when you check it, with a reason and a reference, asked once this session.</p>
              <FormGrid>
                <Field id="graph-reason" label="Reason">
                  <Select value={investigation.reason} onChange={(e) => investigate({ reason: e.target.value as Reason | "" })}>
                    <option value="">Choose…</option>
                    {(Object.keys(REASON_LABELS) as Reason[]).map((r) => (
                      <option key={r} value={r}>
                        {REASON_LABELS[r]}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field id="graph-reference" label="Reference">
                  <Input value={investigation.reference} placeholder={REFERENCE_PLACEHOLDER} onChange={(e) => investigate({ reference: e.target.value })} />
                </Field>
              </FormGrid>
              <Button
                type="button"
                disabled={request === undefined || lookups.busy}
                disabledReason={request === undefined ? "A root table, a sample, a reason and a reference first" : undefined}
                onClick={lookups.check}
              >
                Check sample
              </Button>
            </div>
          )}
          {notices.map((n) => (
            <p key={n} className="hint">
              {n}
            </p>
          ))}
        </section>

        {rootTable && (
          <section className="card" aria-labelledby="graph-title">
            <h2 id="graph-title">Entity graph</h2>
            <p role="status" aria-live="polite" aria-label="Last check" className="hint">
              Last check: {sample.trim() === "" ? "no sample yet." : lastCheck(current, lookups.state)}
            </p>
            {lookups.error && !refusal && (
              <div className="error-state" role="alert">
                <p>{lookups.error.message}</p>
                <Button type="button" variant="outline" onClick={lookups.check}>
                  Retry
                </Button>
              </div>
            )}
            {refusal && (
              <p className="form-error" role="alert">
                The sample was not looked up: {refusal.message}
              </p>
            )}
            {wide ? (
              <EntityGraph label={`Entity graph of ${current.entity || "the new entity"}`} locations={graph.locations} keys={graph.keys} selected={focus?.name ?? ""} onSelect={setSelected} />
            ) : (
              <EntityOutline label={`Outline of ${current.entity || "the new entity"}`} locations={graph.locations} keys={graph.keys} selected={focus?.name ?? ""} onSelect={setSelected} />
            )}
            {focus && focusOutcome && (
              <section aria-label={`${focus.title || focus.name}: details`} className="mt-4">
                <h3>{focus.title || focus.name}</h3>
                <p>
                  {focusOutcome.label}
                  {focusOutcome.detail ? `: ${focusOutcome.detail}` : ""}
                </p>
                <p className="hint">
                  <code>{focus.connector}</code> · <code>{focus.table}</code> · by <code>{focus.keyColumn}</code>
                  {focus.keyFrom ? (
                    <>
                      {" "}
                      from <code>{`${focus.keyFrom.location}.${focus.keyFrom.field}`}</code>
                    </>
                  ) : (
                    ", the root key"
                  )}
                </p>
              </section>
            )}
          </section>
        )}

        {rootTable && (
          <EntityLocations
            application={application.name}
            checks={checks}
            locations={current.locations}
            rootKey={current.rootKey}
            tables={tables}
            catalog={catalogTables}
            rowCeilings={rowCeilings}
            inUse={fieldsInUse(current)}
            onChange={setLocations}
          />
        )}

        {rootTable && (
          <EntityIdentifiers
            rootKey={current.rootKey}
            identifiers={current.identifiers}
            candidates={identifierCandidates(current.locations, tables, current.rootKey)}
            onChange={(identifiers) => update({ identifiers })}
          />
        )}

        {rootTable && (
          <section className="card" aria-labelledby="create-title">
            <h2 id="create-title">Create</h2>
            <FormGrid>
              <Field id="entity-title" label="Title">
                <Input value={current.title} placeholder={entityTitle({ entity: current.entity, title: "" }) || "such as Visitor"} onChange={(e) => update({ title: e.target.value })} />
              </Field>
              <Field id="entity-owner" label="Owning team">
                <Input value={current.owner} onChange={(e) => update({ owner: e.target.value })} />
              </Field>
            </FormGrid>
            {problems.length > 0 ? (
              <ul className="form-error" aria-label="Before it can be created">
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : (
              <>
                {preview === undefined || stale ? (
                  <Button type="button" disabled={taken || save.isPending} onClick={() => save.mutate(undefined)}>
                    Create {current.entity}
                  </Button>
                ) : (
                  <>
                    <SaveChecksPanel report={preview.change.report} loading={save.isPending} error={save.error?.message} onCheck={() => save.mutate(undefined)} />
                    <ChangePreview change={preview.change} stale={stale} />
                    {saveError?.needsAcknowledgement && (
                      <label>
                        <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> Save without the plan check, recorded in
                        the audit with my name
                      </label>
                    )}
                    <Button type="button" disabled={!preview.change.report.can_save || save.isPending} onClick={() => save.mutate(preview.change.base)}>
                      Write {preview.change.path}
                    </Button>
                  </>
                )}
                {saveError && saveError.errors.length > 0 && !saveError.needsAcknowledgement && (
                  <ul className="form-error" aria-label="Failed save checks">
                    {saveError.errors.map((e) => (
                      <li key={`${e.field}: ${e.message}`}>
                        {e.field}: {e.message}
                      </li>
                    ))}
                  </ul>
                )}
                {save.isError && !saveError && (
                  <p className="form-error" role="alert">
                    {save.error.message}
                  </p>
                )}
              </>
            )}
          </section>
        )}
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>New entity</h1>
          <p className="hint">
            {application.title} · tables imported in {environment.title}
          </p>
        </div>
      </div>
      {body}
    </>
  );
}
