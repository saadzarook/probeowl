// Author: @SaadZarook
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { RunbookFilters, RunbookView } from "../api/client";
import { useEntities } from "../api/useEntities";
import { useApplicationProbes } from "../api/useProbes";
import { useRun } from "../api/useRun";
import { useRunbooks, useStepsInRun } from "../api/useRunbooks";
import { CopyButton } from "../components/CopyButton";
import { EmptyState, GuideLink } from "../components/EmptyState";
import { RunbookStep } from "../components/RunbookStep";
import type { NavItem } from "../routes";
import { appliesText, runbookExample } from "../runbooks";
import { useSelection } from "../selection";

/** No runbooks yet: where the file goes, a minimal one built from this application's entity and probe, and the guide. */
function NoRunbooks({ application, title, owner }: { application: string; title: string; owner: string }) {
  const entities = useEntities(application);
  const probes = useApplicationProbes(application);
  const entity = entities.data?.[0];
  const example = runbookExample({
    application,
    owner,
    today: new Date().toISOString().slice(0, 10),
    entity: entity?.entity,
    location: entity ? Object.keys(entity.locations)[0] : undefined,
    probe: probes.data?.find((p) => p.entity === entity?.entity)?.name,
  });
  return (
    <EmptyState
      title="No runbooks in this application"
      message={
        <>
          {title} has no runbooks yet. A runbook is the steps for a known problem, suggested when a run finds it. Add a file under{" "}
          <code>{application}/runbooks/</code>, such as the one below, then load it: Reload configuration on the Connectors screen, or owl reload.
          Runbooks live in git next to the probes, and are reviewed like code.
        </>
      }
    >
      <pre className="runbook-example" tabIndex={0} aria-label="A minimal runbook">
        {example}
      </pre>
      <CopyButton text={example} label="Copy runbook" />
      <GuideLink page="writing-runbooks/">Writing runbooks</GuideLink>
    </EmptyState>
  );
}

function ReviewChips({ runbook }: { runbook: RunbookView }) {
  return (
    <>
      {runbook.review_due && <span className="runbook-flag" data-flag="review">Review due</span>}
      {(runbook.drifted_tables ?? []).length > 0 && <span className="runbook-flag" data-flag="drift">Table drifted</span>}
    </>
  );
}

/** One runbook, with a run's values when it was opened from one. */
function RunbookDetail({ runbook, runId, environmentTitle }: { runbook: RunbookView; runId: string | undefined; environmentTitle: (name: string) => string | undefined }) {
  const [filling, setFilling] = useState(true);
  const run = useRun(runId);
  const usedRun = runId !== undefined && filling ? run.data : undefined;
  const steps = useStepsInRun(usedRun?.id, usedRun ? runbook.runbook : undefined);
  const states = new Map((steps.data ?? []).map((state) => [state.id, state]));
  return (
    <section className="runbook-detail" aria-label={runbook.title}>
      <header className="card runbook-header">
        <div className="runbook-title">
          <h2>{runbook.title}</h2>
          {runId !== undefined && (
            <label>
              <input type="checkbox" checked={filling} onChange={(e) => setFilling(e.target.checked)} /> Fill values from run {runId}
            </label>
          )}
        </div>
        <p className="hint">
          {runbook.application} · owner {runbook.owner} · last verified {runbook.last_verified}
        </p>
        <div className="runbook-applies">
          <span className="hint">Applies when</span>
          {runbook.applies_to.when.map((when) => (
            <code key={when} className="runbook-when">{appliesText(runbook.applies_to.entity, when)}</code>
          ))}
          <span className="hint">Uses tables {(runbook.tables ?? []).join(", ") || "none"}</span>
        </div>
        {runbook.review_due && <p className="summary" role="note">Review due: its last review is over 90 days old.</p>}
        {(runbook.drifted_tables ?? []).length > 0 && (
          <p className="summary" role="note">Check it before relying on it: {(runbook.drifted_tables ?? []).join(", ")} drifted from the catalog.</p>
        )}
      </header>
      {runId !== undefined && run.isError && (
        <div className="error-state" role="alert">
          <p>{run.error.message}</p>
          <button className="btn" type="button" onClick={() => void run.refetch()}>Retry</button>
        </div>
      )}
      {usedRun && steps.isError && (
        <div className="error-state" role="alert">
          <p>{steps.error.message}</p>
          <button className="btn" type="button" onClick={() => void steps.refetch()}>Retry</button>
        </div>
      )}
      {runbook.steps.map((step, index) => (
        <RunbookStep
          key={step.id}
          application={runbook.application}
          runbook={runbook.runbook}
          number={index + 1}
          step={step}
          run={usedRun}
          state={states.get(step.id)}
          environment={usedRun ? environmentTitle(usedRun.environment) : undefined}
        />
      ))}
    </section>
  );
}

/** Runbooks of known problems: every follow-up step, labelled by whether ProbeOwl runs it. */
export function KnowledgeBasePage({ item }: { item: NavItem }) {
  const { application, selectApplication } = useSelection();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [entity, setEntity] = useState("");
  const [table, setTable] = useState("");
  const [identifier, setIdentifier] = useState("");
  const filters: RunbookFilters = {
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(entity ? { entity } : {}),
    ...(table ? { table } : {}),
    ...(identifier.trim() ? { identifier: identifier.trim() } : {}),
  };
  const everything = useRunbooks(application?.name);
  const found = useRunbooks(application?.name, filters);
  const runId = params.get("run") ?? undefined;
  const picked = params.get("runbook") ?? undefined;
  const opened = useRun(runId);
  const openedApplication = opened.data?.application;
  const openedEnvironment = opened.data?.environment;
  // A link to a run opens its application and environment, once: after that the person may choose
  // another. The top bar must never say Production over a run from UAT.
  const followed = useRef<string>();
  useEffect(() => {
    if (runId === undefined || openedApplication === undefined || followed.current === runId) return;
    followed.current = runId;
    selectApplication(openedApplication, openedEnvironment);
  }, [runId, openedApplication, openedEnvironment, selectApplication]);
  // In another application the run's values mean nothing, so its runbooks open without them.
  const usableRun = openedApplication !== undefined && openedApplication !== application?.name ? undefined : runId;
  const list = found.data ?? [];
  const selected = list.find((r) => r.runbook === picked) ?? list[0];
  const entities = [...new Set((everything.data ?? []).map((r) => r.applies_to.entity))].sort();
  const tables = [...new Set((everything.data ?? []).flatMap((r) => r.tables ?? []))].map((t) => t.split(".")[1] ?? t).sort();

  function pick(runbook: string) {
    const next = new URLSearchParams(params);
    next.set("runbook", runbook);
    setParams(next);
  }

  let body;
  if (application === undefined) {
    body = <EmptyState title="Choose an application" message="Runbooks belong to one application." />;
  } else if (found.isError) {
    body = (
      <div className="error-state" role="alert">
        <p>{found.error.message}</p>
        <button className="btn" type="button" onClick={() => void found.refetch()}>Retry</button>
      </div>
    );
  } else if (found.isPending) {
    body = <p aria-busy="true">Loading the runbooks…</p>;
  } else if ((everything.data ?? []).length === 0) {
    body = <NoRunbooks application={application.name} title={application.title} owner={application.owner} />;
  } else {
    body = (
      <div className="tables-layout runbook-layout">
        <div>
          <form className="runbook-filters" role="search" onSubmit={(e) => e.preventDefault()}>
            <label className="field">
              Search
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Symptom, such as not credited" />
            </label>
            <label className="field">
              Entity
              <select value={entity} onChange={(e) => setEntity(e.target.value)}>
                <option value="">Any</option>
                {entities.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            <label className="field">
              Table
              <select value={table} onChange={(e) => setTable(e.target.value)}>
                <option value="">Any</option>
                {[...new Set(tables)].map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            <label className="field">
              Identifier
              <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="topup_id" />
            </label>
          </form>
          {list.length === 0 ? (
            <p className="hint">No runbook matches. Change the filters above.</p>
          ) : (
            <ul className="runbook-list">
              {list.map((runbook) => (
                <li key={runbook.runbook}>
                  <button type="button" aria-pressed={runbook.runbook === selected?.runbook} onClick={() => pick(runbook.runbook)}>
                    <strong>{runbook.title}</strong>
                    <span className="hint">{runbook.steps.length} steps · {runbook.owner}</span>
                    <span className="runbook-flags"><ReviewChips runbook={runbook} /></span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {selected ? (
          <RunbookDetail
            key={`${selected.runbook}/${usableRun ?? ""}`}
            runbook={selected}
            runId={usableRun}
            environmentTitle={(name) => application.environments.find((e) => e.name === name)?.title}
          />
        ) : null}
      </div>
    );
  }

  return (
    <>
      <div className="page-header">
        <h1>{item.label}</h1>
        <p>{item.subtitle} · write queries are never run</p>
      </div>
      {body}
    </>
  );
}
