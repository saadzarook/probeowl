// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { pickMatches, type RunSummary } from "../api/client";
import { useEntity } from "../api/useEntities";
import { useRun } from "../api/useRun";
import { useRuns } from "../api/useRuns";
import { EmptyState } from "../components/EmptyState";
import { HealthyComparisonSection } from "../components/HealthyComparison";
import { InspectResult } from "../components/InspectResult";
import { RunResult } from "../components/RunResult";
import { SuggestedNextSteps } from "../components/SuggestedNextSteps";
import { REASON_LABELS } from "../reasons";
import type { NavItem } from "../routes";
import { formatTime, titleCase, withCaveat } from "../runFormat";
import { useSelection } from "../selection";

/** "Stopped" for a run a kill switch or the time limit cut short, never a bare Failed. */
export function listedStatus(run: Pick<RunSummary, "status" | "stop_reason">): { label: string; key: string } {
  if (run.stop_reason) return { label: "Stopped", key: "stopped" };
  return { label: titleCase(run.status), key: run.status };
}

function RunRow({ run }: { run: RunSummary }) {
  const status = listedStatus(run);
  const why = run.stop_reason ?? run.skip_reason;
  return (
    <tr>
      <td>
        <span className="status" data-status={status.key} data-caveat={run.caveat ? true : undefined}>
          {withCaveat(status.label, run.caveat)}
        </span>
        {why && <span className="hint block">{why}</span>}
      </td>
      <td>
        <Link to={`/runs?run=${encodeURIComponent(run.id)}`}>{run.probe_title}</Link>
        {run.draft && <span className="draft-tag">Draft</span>}
      </td>
      <td>
        <code>{run.subject ?? run.identifier_value}</code>
      </td>
      <td>
        {run.reason ? REASON_LABELS[run.reason] : "—"}
        {run.reference && <span className="hint"> · {run.reference}</span>}
      </td>
      <td className="nowrap">{formatTime(run.started_at)}</td>
      <td>{run.actor}</td>
    </tr>
  );
}

function RunList() {
  const { application, environment } = useSelection();
  const runs = useRuns(application?.name, environment?.name);
  if (application === undefined || environment === undefined) {
    return <EmptyState title="Choose an application" message="Runs are listed for one application and environment." />;
  }
  if (runs.isPending) return <p aria-busy="true">Loading runs…</p>;
  if (runs.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{runs.error.message}</p>
        <button className="btn" type="button" onClick={() => void runs.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  const listed = runs.data.pages.flatMap((page) => page.runs);
  if (listed.length === 0) {
    return (
      <EmptyState title={`No runs yet in ${application.title} ${environment.title}`} message="A run appears here once someone inspects or validates a subject.">
        <Link to="/validate">Validate</Link>
        <Link to="/inspect">Inspect</Link>
      </EmptyState>
    );
  }
  return (
    <section className="card">
      <div className="table-scroll">
        <table className="run-list">
          <thead>
            <tr>
              <th scope="col">Status</th>
              <th scope="col">Probe</th>
              <th scope="col">Subject</th>
              <th scope="col">Reason · reference</th>
              <th scope="col">Started</th>
              <th scope="col">By</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </tbody>
        </table>
      </div>
      {runs.hasNextPage && (
        <button className="btn btn-outline" type="button" disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
          {runs.isFetchingNextPage ? "Loading…" : "Show older"}
        </button>
      )}
    </section>
  );
}

/** One run, in the same result view as the screen that started it. */
function OpenedRun({ runId }: { runId: string }) {
  const { application } = useSelection();
  const navigate = useNavigate();
  const run = useRun(runId);
  const entity = useEntity(run.data?.application, run.data?.kind === "inspect" ? run.data.entity : undefined);
  const openFirst = (ids: string[]) => {
    const [first] = ids;
    if (first !== undefined) void navigate(`/runs?run=${encodeURIComponent(first)}`);
  };
  const pick = useMutation({
    mutationFn: ({ id, subjects }: { id: string; subjects: string[] }) => pickMatches(id, subjects),
    onSuccess: openFirst,
  });
  const production = application?.environments.find((e) => e.name === run.data?.environment)?.production === true;
  let result;
  if (run.data?.kind === "inspect") {
    result = entity.data ? (
      <InspectResult runId={runId} entity={entity.data} onInspect={(id, subjects) => pick.mutate({ id, subjects })} onStarted={openFirst} />
    ) : (
      <p aria-busy="true">Loading run…</p>
    );
  } else {
    result = <RunResult runId={runId} />;
  }
  return (
    <>
      <p>
        <Link to="/runs">← All runs</Link>
      </p>
      {result}
      <HealthyComparisonSection runId={runId} production={production} />
      <SuggestedNextSteps runId={runId} />
    </>
  );
}

/** Runs: recent investigations newest first, each opening its result. */
export function RunsPage({ item }: { item: NavItem }) {
  const [params] = useSearchParams();
  const runId = params.get("run");
  return (
    <>
      <div className="page-header">
        <h1>Runs</h1>
        <p>{item.subtitle}</p>
      </div>
      {runId ? <OpenedRun runId={runId} /> : <RunList />}
    </>
  );
}
