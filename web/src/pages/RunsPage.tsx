// Author: @SaadZarook
import { Badge, Button, type Column, ListOrTable } from "../ui";
import { RunStatusChip } from "../components/RunStatusChip";
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

/** A run's columns: a table from 768 px, cards below (rule 6). */
const RUN_COLUMNS: Column<RunSummary>[] = [
  {
    header: "Status",
    cell: (run) => {
      const status = listedStatus(run);
      const why = run.stop_reason ?? run.skip_reason;
      return (
        <>
          <RunStatusChip status={status.key} caveat={Boolean(run.caveat)}>
            {withCaveat(status.label, run.caveat)}
          </RunStatusChip>
          {why && <span className="mt-1 block max-w-80 text-sm text-muted">{why}</span>}
        </>
      );
    },
  },
  {
    header: "Probe",
    cell: (run) => (
      <>
        <Link to={`/runs?run=${encodeURIComponent(run.id)}`}>{run.probe_title}</Link>
        {run.draft && (
          <Badge className="ml-2" tone="neutral">
            Draft
          </Badge>
        )}
      </>
    ),
  },
  { header: "Subject", cell: (run) => <code className="font-mono wrap-anywhere">{run.subject ?? run.identifier_value}</code> },
  {
    header: "Reason · reference",
    cell: (run) => (
      <>
        {run.reason ? REASON_LABELS[run.reason] : "—"}
        {run.reference && <span className="text-muted"> · {run.reference}</span>}
      </>
    ),
  },
  { header: "Started", cell: (run) => formatTime(run.started_at) },
  { header: "By", cell: (run) => run.actor },
];

// Drafts' test runs (the Probe editor's Test run, the entity graph's lookups) are left out by the
// server unless asked for, so that each page is full and Show older is right (#164). In the URL, so
// that a reload or a link keeps it.
const SHOW_DRAFTS = "drafts";

function DraftsFilter({ shown, onChange }: { shown: boolean; onChange: (shown: boolean) => void }) {
  return (
    <label className="mb-3 flex items-center gap-2 text-sm text-foreground">
      <input type="checkbox" checked={shown} onChange={(event) => onChange(event.target.checked)} />
      Show draft runs
    </label>
  );
}

function RunList() {
  const { application, environment } = useSelection();
  const [query, setQuery] = useSearchParams();
  const drafts = query.get(SHOW_DRAFTS) === "show";
  const runs = useRuns(application?.name, environment?.name, drafts);
  const showDrafts = (shown: boolean) =>
    setQuery(
      (current) => {
        const next = new URLSearchParams(current);
        if (shown) next.set(SHOW_DRAFTS, "show");
        else next.delete(SHOW_DRAFTS);
        return next;
      },
      { replace: true },
    );
  if (application === undefined || environment === undefined) {
    return <EmptyState title="Choose an application" message="Runs are listed for one application and environment." />;
  }
  const filter = <DraftsFilter shown={drafts} onChange={showDrafts} />;
  if (runs.isPending) return <p aria-busy="true">Loading runs…</p>;
  if (runs.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{runs.error.message}</p>
        <Button type="button" onClick={() => void runs.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  const listed = runs.data.pages.flatMap((page) => page.runs);
  if (listed.length === 0) {
    return (
      <>
        {filter}
        <EmptyState
          title={`No runs yet in ${application.title} ${environment.title}`}
          message={drafts ? "A run appears here once someone inspects or validates a subject." : "A run appears here once someone inspects or validates a subject. Draft runs are hidden."}
        >
          <Link to="/validate">Validate</Link>
          <Link to="/inspect">Inspect</Link>
        </EmptyState>
      </>
    );
  }
  return (
    <section className="card">
      {filter}
      <ListOrTable label="Runs" rows={listed} columns={RUN_COLUMNS} rowKey={(run) => run.id} empty="No runs yet." />
      {runs.hasNextPage && (
        <Button variant="outline" type="button" disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
          {runs.isFetchingNextPage ? "Loading…" : "Show older"}
        </Button>
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
