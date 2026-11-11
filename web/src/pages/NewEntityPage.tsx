// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { saveEntity, SaveError, type EntityChange } from "../api/client";
import { notImported, useCatalog, useSchemas } from "../api/useCatalog";
import { useConnectors } from "../api/useConnectors";
import { useEntities } from "../api/useEntities";
import { ChangePreview } from "../components/ChangePreview";
import { EmptyState } from "../components/EmptyState";
import { EntityIdentifiers } from "../components/EntityIdentifiers";
import { EntityLocations } from "../components/EntityLocations";
import { SaveChecksPanel } from "../components/SaveChecksPanel";
import { draftProblems, entityTitle, entityYaml, type DraftIdentifier, type DraftLocation, type EntityDraft } from "../entityDraft";
import { useEntityCheck } from "../api/useEntityCheck";
import { defaultRootKey, identifierCandidates, importedTables, pretickedIdentifiers, rootLocation, tableReference } from "../followKeys";
import { failuresByLocation } from "../locationChecks";
import { useSelection } from "../selection";

const STEPS = ["Name", "Root table", "Locations", "Identifiers", "Preview"] as const;

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

/** A new entity, step by step from the imported schema: name, root table, locations, identifiers, then preview and create. */
export function NewEntityPage() {
  const { application, environment } = useSelection();
  const connectors = useConnectors(application?.name, environment?.name);
  // An entity that exists is changed with its probes, so its id is refused here, as it is typed.
  const existing = useEntities(application?.name);
  const catalog = useCatalog(application?.name);
  const sql = (connectors.data ?? []).filter((c) => c.type !== "rest").map((c) => c.connector);
  const schemas = useSchemas(application?.name, sql, environment?.name, new Set(sql));
  const snapshots = [...schemas.values()].flatMap((r) => (r.snapshot ? [r.snapshot] : []));
  // In the words of the Tables screen, with the command that imports it.
  const notices = sql.filter((c) => schemas.get(c)?.notice !== undefined).map((c) => notImported(application?.name ?? "", c, environment?.name ?? ""));
  const tables = importedTables(snapshots);
  const catalogTables = catalog.data?.tables ?? [];
  const rowCeilings = new Map((connectors.data ?? []).flatMap((c) => (c.row_ceiling ? [[c.connector, c.row_ceiling] as const] : [])));

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<EntityDraft>({ entity: "", title: "", owner: "", rootKey: "", locations: [], identifiers: [] });
  const [root, setRoot] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [preview, setPreview] = useState<{ change: EntityChange; yaml: string }>();
  const client = useQueryClient();
  const yaml = entityYaml(draft);
  const save = useMutation({
    mutationFn: (base: string | undefined) =>
      saveEntity(application?.name ?? "", draft.entity, {
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

  // From the Locations step on, every change is checked as it would be saved, so a failing location shows where it is.
  const checkable = step >= 2 && !(save.isSuccess && save.data.written) && draftProblems({ ...draft, owner: draft.owner || (application?.owner ?? "") }).length === 0;
  const check = useEntityCheck(application?.name ?? "", draft.entity, yaml, environment?.name ?? "", catalog.dataUpdatedAt, application !== undefined && environment !== undefined && checkable);

  if (application === undefined || environment === undefined) {
    return <EmptyState level={1} title="Choose an application" message="An entity belongs to one application." />;
  }
  const owner = draft.owner || application.owner;
  const current: EntityDraft = { ...draft, owner };
  const update = (change: Partial<EntityDraft>) => {
    setDraft({ ...current, ...change });
    setPreview(undefined);
  };
  const chooseRoot = (key: string) => {
    setRoot(key);
    const table = tables.find((t) => `${t.connector}.${t.table}` === key);
    if (!table) return update({ locations: [], identifiers: [] });
    const rootKey = defaultRootKey(current.entity, table);
    const location = rootLocation(table, tables, catalogTables, rootKey);
    update({ rootKey, locations: [location], identifiers: [{ name: rootKey, unique: true, pii: false, pattern: "" }] });
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
    const selected = (i: DraftIdentifier) => locations.some((l) => l.name === i.via?.location && l.columns.some((c) => c.selected && c.field === i.via?.field));
    const kept = current.identifiers.filter((i) => i.via === undefined || selected(i));
    // A location just added brings its unique, non-PII columns as identifiers, ticked: a card ID finds its visitor.
    const added = locations.filter((l) => !current.locations.some((c) => c.name === l.name));
    update({ locations, identifiers: [...kept, ...pretickedIdentifiers(added, tables, current.rootKey, kept)] });
  };
  const problems = draftProblems(current);
  const taken = (existing.data ?? []).some((e) => e.entity === current.entity);
  const done = save.isSuccess && save.data.written;
  const stale = preview !== undefined && preview.yaml !== yaml;
  const saveError = save.error instanceof SaveError ? save.error : undefined;
  const failures = failuresByLocation(check.data?.failures ?? [], current.locations.map((l) => l.name));
  const failing = [...failures.byLocation.keys()];
  const checks = {
    checking: checkable && (check.isFetching || check.data === undefined),
    byLocation: failures.byLocation,
    general: failures.general,
    ...(check.isError ? { error: check.error.message } : {}),
    onRetry: () => void check.refetch(),
  };

  let body;
  if (connectors.isPending || catalog.isPending) {
    body = <p aria-busy="true">Loading the imported tables…</p>;
  } else if (connectors.isError) {
    body = (
      <div className="error-state" role="alert">
        <p>{connectors.error.message}</p>
        <button className="btn" type="button" onClick={() => void connectors.refetch()}>Retry</button>
      </div>
    );
  } else if (done) {
    body = (
      <section className="card" role="status" aria-label="Entity created">
        <h2>{current.entity} was created</h2>
        <p>
          Written to <code>{save.data.path}</code>. Now write its first probe.
        </p>
        <RouterLink className="btn" to={`/probes?new=inspect&entity=${encodeURIComponent(current.entity)}`}>
          Write a probe for {current.entity}
        </RouterLink>
      </section>
    );
  } else if (tables.length === 0) {
    body = (
      <EmptyState title={`No tables imported in ${environment.title}`} message="The wizard builds an entity from imported schemas. Import a SQL connector's schema first.">
        {notices.map((n) => (
          <p key={n} className="hint">{n}</p>
        ))}
        <RouterLink to="/tables">Tables: Import from replica</RouterLink>
      </EmptyState>
    );
  } else {
    body = (
      <>
        <ol className="wizard-steps" aria-label="Steps">
          {STEPS.map((label, i) => (
            <li key={label} aria-current={i === step ? "step" : undefined}>
              {label}
            </li>
          ))}
        </ol>
        {step === 0 && (
          <section className="card wizard-form" aria-labelledby="name-step">
            <h2 id="name-step">Name</h2>
            <label>
              Entity id
              <input
                value={current.entity}
                placeholder="such as visitor or topup"
                aria-invalid={taken ? true : undefined}
                aria-describedby={taken ? "entity-taken" : undefined}
                onChange={(e) => update({ entity: e.target.value.trim() })}
              />
            </label>
            {taken && (
              <p className="form-error" id="entity-taken" role="alert">
                {current.entity} already exists:{" "}
                <RouterLink to={`/probes?new=inspect&entity=${encodeURIComponent(current.entity)}`}>open it</RouterLink>, or choose another name.
              </p>
            )}
            <label>
              Title
              <input value={current.title} placeholder={entityTitle({ entity: current.entity, title: "" }) || "such as Visitor"} onChange={(e) => update({ title: e.target.value })} />
            </label>
            <label>
              Owning team
              <input value={current.owner} onChange={(e) => update({ owner: e.target.value })} />
            </label>
          </section>
        )}
        {step === 1 && (
          <section className="card wizard-form" aria-labelledby="root-step">
            <h2 id="root-step">Root table</h2>
            <p className="hint">The table one subject is a row of. Lookups start from its key, the root key.</p>
            <label>
              Root table
              <select value={root} onChange={(e) => chooseRoot(e.target.value)}>
                <option value="">Choose…</option>
                {tables.map((t) => (
                  <option key={`${t.connector}.${t.table}`} value={`${t.connector}.${t.table}`}>
                    {t.connector} · {tableReference(t, tables)}
                  </option>
                ))}
              </select>
            </label>
            {current.locations[0] && (
              <label>
                Root key, the name {current.locations[0].keyColumn} goes by
                <input value={current.rootKey} onChange={(e) => renameRootKey(e.target.value.trim())} />
              </label>
            )}
            {notices.map((n) => (
              <p key={n} className="hint">{n}</p>
            ))}
          </section>
        )}
        {step === 2 && (
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
        {step === 3 && (
          <EntityIdentifiers
            rootKey={current.rootKey}
            identifiers={current.identifiers}
            candidates={identifierCandidates(current.locations, tables, current.rootKey)}
            onChange={(identifiers) => update({ identifiers })}
          />
        )}
        {step === 4 && (
          <section className="card" aria-labelledby="preview-step">
            <h2 id="preview-step">Preview</h2>
            <pre className="yaml-preview" tabIndex={0} aria-label={`${current.entity}.yaml`}>
              {yaml}
            </pre>
            {failing.length > 0 && (
              <div className="form-error" role="alert">
                <p>
                  {failing.join(", ")} {failing.length === 1 ? "fails" : "fail"} a save check. Fix or remove {failing.length === 1 ? "it" : "them"} on the Locations step:
                  nothing is left out of the entity without you.
                </p>
                <button className="btn btn-outline" type="button" onClick={() => setStep(2)}>
                  Back to Locations
                </button>
              </div>
            )}
            {problems.length > 0 ? (
              <ul className="form-error">
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : (
              <>
                <SaveChecksPanel report={preview?.change.report} loading={save.isPending} error={save.error?.message} onCheck={() => save.mutate(undefined)} />
                {saveError && saveError.errors.length > 0 && !saveError.needsAcknowledgement && (
                  <ul className="form-error" aria-label="Failed save checks">
                    {saveError.errors.map((e) => (
                      <li key={`${e.field}: ${e.message}`}>
                        {e.field}: {e.message}
                      </li>
                    ))}
                  </ul>
                )}
                {preview && <ChangePreview change={preview.change} stale={stale} />}
                {saveError?.needsAcknowledgement && (
                  <label>
                    <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> Save without the plan check, recorded in
                    the audit with my name
                  </label>
                )}
                <button
                  className="btn"
                  type="button"
                  disabled={!preview || stale || !preview.change.report.can_save || save.isPending}
                  onClick={() => preview && save.mutate(preview.change.base)}
                >
                  Create {current.entity}
                </button>
              </>
            )}
          </section>
        )}
        <div className="wizard-nav">
          <button className="btn btn-outline" type="button" disabled={step === 0} onClick={() => setStep(step - 1)}>
            Back
          </button>
          {step < STEPS.length - 1 && (
            <button className="btn" type="button" disabled={(step === 0 && taken) || (step === 1 && current.locations.length === 0)} onClick={() => setStep(step + 1)}>
              Next: {STEPS[step + 1]}
            </button>
          )}
        </div>
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
