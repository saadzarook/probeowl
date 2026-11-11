// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchSuggestedRunbooks, type RunbookView } from "../api/client";
import { useRun } from "../api/useRun";
import { appliesText } from "../runbooks";

const KIND_WORDS: Record<RunbookView["steps"][number]["kind"], string> = {
  read_query: "read",
  external_query: "external",
  manual: "manual",
  write_query: "write",
  probe: "probe",
};

/** "6 steps: 2 read, 1 external, 1 manual, 1 write, 1 probe". */
function stepsText(runbook: RunbookView): string {
  const counts = new Map<string, number>();
  for (const step of runbook.steps) {
    const word = KIND_WORDS[step.kind];
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  const parts = [...counts].map(([word, count]) => `${count} ${word}`);
  return `${runbook.steps.length} ${runbook.steps.length === 1 ? "step" : "steps"}: ${parts.join(", ")}`;
}

function Suggestions({ runId }: { runId: string }) {
  const found = useQuery({
    queryKey: ["suggested-runbooks", runId],
    queryFn: () => fetchSuggestedRunbooks(runId),
    retry: false,
  });
  if (found.isPending) return <p aria-busy="true">Looking for runbooks…</p>;
  if (found.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{found.error.message}</p>
        <button className="btn" type="button" onClick={() => void found.refetch()}>Retry</button>
      </div>
    );
  }
  const suggestions = found.data;
  if (suggestions.length === 0) return <p className="hint">No runbook matches this run's outcomes.</p>;
  return (
    <ul className="next-steps">
      {suggestions.map(({ runbook, matched }, index) => (
        <li key={runbook.runbook}>
          <div>
            <strong>{runbook.title}</strong>
            <p className="hint">
              Matches {matched.map((when) => appliesText(runbook.applies_to.entity, when).split(" · ")[1]).join(", ")} · {stepsText(runbook)}
              {runbook.review_due ? " · review due" : ""}
            </p>
          </div>
          <Link className={index === 0 ? "btn" : "btn btn-outline"} to={`/knowledge-base?runbook=${encodeURIComponent(runbook.runbook)}&run=${encodeURIComponent(runId)}`}>
            Open with values
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Under a finished run: the runbooks whose applies_to matches its outcomes, so the next step is where the failure is. */
export function SuggestedNextSteps({ runId }: { runId: string }) {
  const run = useRun(runId);
  const data = run.data;
  if (data === undefined || data.subject == null || (data.status !== "passed" && data.status !== "failed")) return null;
  return (
    <section className="card next-steps-card" aria-label="Suggested next steps">
      <header className="compare-header">
        <h2>Suggested next steps</h2>
        <p className="hint">Runbooks that match this run's outcomes</p>
      </header>
      <Suggestions runId={runId} />
    </section>
  );
}
