// Author: @SaadZarook
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Entity } from "../api/client";
import { useEntities, useEntity, useEntityYaml } from "../api/useEntities";
import { fetchProbeYaml } from "../api/client";
import { useApplicationProbes } from "../api/useProbes";
import { useSaveChecks } from "../api/useSaveChecks";
import { EmptyState, GuideLink } from "../components/EmptyState";
import { NewProbeForm, type NewProbe } from "../components/NewProbeForm";
import { SaveProbeDialog } from "../components/SaveProbeDialog";
import { SaveChecksPanel } from "../components/SaveChecksPanel";
import { TestRunBox } from "../components/TestRunBox";
import { YamlEditor } from "../components/YamlEditor";
import { keyGraph, probeName, probeTemplate, readFields, sameText, setField, type ProbeKindChoice } from "../draftYaml";
import type { NavItem } from "../routes";
import { useSelection } from "../selection";

type Tab = "yaml" | "entity" | "graph";
const TABS: { id: Tab; label: string }[] = [
  { id: "yaml", label: "YAML" },
  { id: "entity", label: "Entity YAML" },
  { id: "graph", label: "Key graph" },
];

function ErrorBlock({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="error-state" role="alert">
      <p>{message}</p>
      <button className="btn" type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

/** Connectors an Environments check says are not bound, by environment name. */
function unboundBy(checks: { check: string; message: string; environment?: string | null }[]): Map<string, string> {
  const unbound = new Map<string, string>();
  for (const c of checks) {
    const connector = /([\w-]+) not bound/.exec(c.message)?.[1];
    if (c.check === "Environments" && c.environment && connector) unbound.set(c.environment, connector);
  }
  return unbound;
}

const firstLocation = (entity: Entity | undefined) => Object.keys(entity?.locations ?? {})[0] ?? "record";

/** An entity's locations and their fields, as expectations write them: ledger, ledger.amount. */
function locationNames(entity: Entity | undefined): string[] {
  return Object.entries(entity?.locations ?? {}).flatMap(([name, l]) => [name, ...(l.exposes ?? []).map((f) => `${name}.${f}`)]);
}

/** A new probe being named, in the application it was asked in. */
interface Naming {
  scope: string | undefined;
  entity: string;
  kind: ProbeKindChoice;
}

/** The new probe a ?new=<kind>&entity=<name> link asks to name, once the entities are loaded. */
function askedProbe(params: URLSearchParams, entities: Entity[] | undefined): { entity: string; kind: ProbeKindChoice } | undefined {
  const kind = params.get("new");
  if (kind !== "inspect" && kind !== "validate") return undefined;
  const chosen = entities?.find((e) => e.entity === params.get("entity")) ?? entities?.[0];
  return chosen && { entity: chosen.entity, kind };
}

/** The Probe editor: YAML with its save checks and a test run of the draft. */
export function ProbeEditorPage({ item }: { item: NavItem }) {
  const { application, environment } = useSelection();
  const name = application?.name;
  const entities = useEntities(name);
  const probes = useApplicationProbes(name);
  const queryClient = useQueryClient();

  const [draft, setDraft] = useState<{ scope: string | undefined; yaml: string }>();
  // A new probe is named before its draft starts. ?new=validate&entity=topup, from an empty probe list
  // elsewhere, asks for that one until a draft is started or opened.
  const [params] = useSearchParams();
  // undefined: nothing asked yet, so a ?new= link may ask; "done": started, opened or cancelled.
  const [naming, setNaming] = useState<Naming | "done">();
  const asked = askedProbe(params, entities.data);
  let pending: Naming | undefined = naming === "done" ? undefined : naming;
  if (naming === undefined && draft === undefined && asked) pending = { scope: name, ...asked };
  const namingNow = pending?.scope === name ? pending : undefined;
  const probeYaml = draft !== undefined && draft.scope === name ? draft.yaml : undefined;
  const setProbeYaml = (yaml: string) => setDraft({ scope: name, yaml });
  const [entityEdit, setEntityEdit] = useState<{ entity: string; text: string }>();
  const [tab, setTab] = useState<Tab>("yaml");
  const [saving, setSaving] = useState(false);
  // The file the draft came from, which is what saving replaces. New drafts have none.
  const [opened, setOpened] = useState<{ application: string; name: string; text: string }>();
  const [openError, setOpenError] = useState<{ probe: string; message: string }>();

  const fields = probeYaml === undefined ? undefined : readFields(probeYaml);
  const entityName = fields?.entity || undefined;
  const entity = useEntity(name, entityName);
  const entityFile = useEntityYaml(name, entityName);
  const loadedYaml = entityFile.data?.text;
  const edited = entityEdit !== undefined && entityEdit.entity === entityName ? entityEdit : undefined;
  const entityText = edited ? edited.text : loadedYaml;
  const entityChanged = edited !== undefined && loadedYaml !== undefined && !sameText(edited.text, loadedYaml);

  const checks = useSaveChecks(name ?? "", probeYaml ?? "", entityChanged ? entityText : undefined, environment?.name);
  const enabled = name !== undefined && probeYaml !== undefined;
  const savedName = probeYaml === undefined ? "" : probeName(probeYaml);
  const isOpened = opened !== undefined && opened.application === name && opened.name === savedName;
  const existing = probes.data?.some((p) => p.name === savedName) === true;
  let saveBlocked: string | undefined;
  if (existing && !isOpened) saveBlocked = `A probe named ${savedName} already exists: open it to edit it.`;
  else if (isOpened && probeYaml !== undefined && sameText(probeYaml, opened.text) && !entityChanged) saveBlocked = "Nothing to save";
  else if (!isOpened && fields !== undefined && fields.title.trim() === "") saveBlocked = "Give the probe a title to save it";
  else if (checks.data?.can_save !== true) saveBlocked = "Fix the failed checks to save";
  const canSave = enabled && environment !== undefined && savedName !== "" && saveBlocked === undefined;

  /** Open the probe's real file: anything rebuilt from its summary would lose its steps on saving. */
  async function openProbe(probe: string) {
    if (name === undefined || probe === "") return;
    setOpenError(undefined);
    try {
      const file = await fetchProbeYaml(name, probe);
      setProbeYaml(file.text);
      setNaming("done");
      setOpened({ application: name, name: probe, text: file.text });
      setEntityEdit(undefined);
    } catch (e) {
      setOpenError({ probe, message: e instanceof Error ? e.message : "The probe's file could not be opened." });
    }
  }

  /** Ask for the new probe's id and title; its draft starts once they are given. */
  function newProbe() {
    const first = entities.data?.[0];
    if (!first) return;
    setNaming({ scope: name, entity: entityName ?? first.entity, kind: "inspect" });
    setOpenError(undefined);
  }

  function startDraft(chosen: NewProbe) {
    if (!application) return;
    const entity = entities.data?.find((e) => e.entity === chosen.entity);
    setProbeYaml(probeTemplate(chosen.entity, chosen.kind, application.owner, chosen.probe, firstLocation(entity), chosen.title));
    setNaming("done");
    setOpened(undefined);
    setEntityEdit(undefined);
  }

  let body;
  if (application === undefined) {
    body = <EmptyState title="Choose an application" message="Probes belong to one application." />;
  } else if (entities.isPending || probes.isPending) {
    body = <p aria-busy="true">Loading probes…</p>;
  } else if (entities.isError || probes.isError) {
    body = (
      <ErrorBlock
        message={(entities.error ?? probes.error)?.message ?? "Probes could not be loaded."}
        onRetry={() => void queryClient.refetchQueries({ predicate: (q) => q.state.status === "error" })}
      />
    );
  } else if (entities.data.length === 0) {
    body = (
      <EmptyState
        title={`${application.title} has no entities yet`}
        message={
          <>
            A probe checks one entity: what a subject is and where its data lives. Create one from the imported tables, or add{" "}
            <code>{application.name}/entities/&lt;name&gt;.yaml</code> yourself.
          </>
        }
      >
        <Link className="btn" to="/probes/new-entity">
          New entity
        </Link>
        <GuideLink page="concepts/">Concepts</GuideLink>
      </EmptyState>
    );
  } else if (namingNow) {
    body = (
      <NewProbeForm
        entities={entities.data}
        taken={probes.data.map((p) => p.name)}
        initial={namingNow}
        onStart={startDraft}
        onCancel={() => setNaming("done")}
      />
    );
  } else if (!enabled) {
    body = <EmptyState title="Pick a probe or start a new one" message="Open a probe above to edit it, or start a new draft." />;
  } else {
    const unbound = unboundBy(checks.data?.checks ?? []);
    const checked = (env: string) => (fields?.environments ? fields.environments.includes(env) : !unbound.has(env));
    const toggle = (env: string) => {
      const current = application.environments.map((e) => e.name).filter((e) => checked(e));
      setProbeYaml(setField(probeYaml, "environments", current.includes(env) ? current.filter((e) => e !== env) : [...current, env]));
    };
    const graph = keyGraph(entityText, entity.data);
    body = (
      <div className="editor-grid">
        <section className="card editor-fields" aria-label="Probe settings">
          <label>
            Title
            <input value={fields?.title ?? ""} placeholder="What people read in Runs" onChange={(e) => setProbeYaml(setField(probeYaml, "title", e.target.value))} disabled={!fields} />
          </label>
          <label>
            Entity
            <select value={fields?.entity ?? ""} onChange={(e) => setProbeYaml(setField(probeYaml, "entity", e.target.value))} disabled={!fields}>
              {entities.data.map((e) => (
                <option key={e.entity} value={e.entity}>
                  {e.entity}
                </option>
              ))}
            </select>
          </label>
          <label>
            Kind
            <select value={fields?.kind ?? ""} onChange={(e) => setProbeYaml(setField(probeYaml, "kind", e.target.value))} disabled={!fields}>
              <option value="inspect">Inspect · current state</option>
              <option value="validate">Validate · one flow</option>
            </select>
          </label>
          <label>
            Owner
            <input value={fields?.owner ?? ""} onChange={(e) => setProbeYaml(setField(probeYaml, "owner", e.target.value))} disabled={!fields} />
          </label>
          <fieldset>
            <legend>Enabled in</legend>
            {application.environments.map((env) => (
              <label key={env.name} className="check-option">
                <input type="checkbox" checked={checked(env.name) && !unbound.has(env.name)} disabled={!fields || unbound.has(env.name)} onChange={() => toggle(env.name)} />
                {env.title}
                {unbound.has(env.name) && <span className="hint"> · {unbound.get(env.name)} not bound</span>}
              </label>
            ))}
          </fieldset>
        </section>

        <section className="card editor-centre" aria-label="Editor panel">
          <div className="editor-tabs">
            <div role="tablist" aria-label="Editor">
              {TABS.map((t) => (
                <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
            <span className="hint">
              {entityName ? `Uses entity ${entityName} · ${graph.length} ${graph.length === 1 ? "location" : "locations"}` : "No entity"}
            </span>
          </div>
          {/* Both editors stay mounted while you switch tabs, so their undo history does too. */}
          <div hidden={tab !== "yaml"}>
            <YamlEditor
              label="Probe YAML"
              value={probeYaml}
              onChange={setProbeYaml}
              completions={locationNames(entity.data)}
              saved={opened !== undefined && isOpened ? opened.text : undefined}
            />
          </div>
          <div hidden={tab !== "entity"}>
            {entityFile.isPending && entityName ? (
              <p aria-busy="true">Loading entity…</p>
            ) : entityFile.isError ? (
              <ErrorBlock message={entityFile.error.message} onRetry={() => void entityFile.refetch()} />
            ) : (
              <YamlEditor label="Entity YAML" value={entityText ?? ""} onChange={(text) => entityName && setEntityEdit({ entity: entityName, text })} saved={loadedYaml} />
            )}
          </div>
          {tab === "graph" && (
            <ul className="key-graph" aria-label="Key graph">
              {graph.length === 0 && <li className="hint">No locations.</li>}
              {graph.map((g) => (
                <li key={g.location}>
                  <code>{g.location}</code> <span className="hint">{g.using}</span>
                  {g.keyFrom ? <span> ← <code>{g.keyFrom}</code></span> : <span className="hint"> · root key</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="editor-side">
          <SaveChecksPanel
            report={checks.data}
            loading={checks.isFetching}
            error={checks.isError ? checks.error.message : undefined}
            onCheck={() => void checks.refetch()}
          />
          <TestRunBox probe={probeYaml} entity={entityChanged ? entityText : undefined} rootKey={entity.data?.root_key} />
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-header editor-header">
        <div>
          <p className="hint">{application?.title ?? ""} / {item.label}</p>
          <h1>{(probeYaml !== undefined && probeName(probeYaml)) || item.label}</h1>
        </div>
        <div className="editor-actions">
          <Link className="btn btn-outline" to="/probes/new-entity">
            New entity…
          </Link>
          <label>
            Open probe
            <select value="" onChange={(e) => void openProbe(e.target.value)} disabled={!probes.data?.length}>
              <option value="">Select a probe</option>
              {(probes.data ?? []).map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-outline" type="button" onClick={() => newProbe()} disabled={!entities.data?.length}>
            New probe
          </button>
          <button className="btn" type="button" disabled={!canSave} onClick={() => setSaving(true)}>
            Save
          </button>
        </div>
      </div>
      {enabled && saveBlocked && <p className="hint" role="status">{saveBlocked}</p>}
      {openError && <ErrorBlock message={openError.message} onRetry={() => void openProbe(openError.probe)} />}
      {body}
      {saving && name !== undefined && probeYaml !== undefined && environment !== undefined && (
        <SaveProbeDialog
          application={name}
          probeName={savedName}
          probe={probeYaml}
          entity={entityChanged ? entityText : undefined}
          environment={environment.name}
          production={environment.production}
          onSaved={() => {
            setOpened({ application: name, name: savedName, text: probeYaml });
            setEntityEdit(undefined);
          }}
          onClose={() => setSaving(false)}
        />
      )}
    </>
  );
}
