// Author: @SaadZarook
import { useState, type FormEvent } from "react";
import type { ComparedField, HealthyAttempt } from "../api/client";
import { useHealthyComparison, useStartHealthyComparison } from "../api/useHealthyComparison";
import { useRun } from "../api/useRun";
import { nothingHighlighted, partDifferences, RESULT_TITLES, shownValue, subtitle } from "../healthyComparison";
import { titleCase } from "../runFormat";

/** One compared field, or a whole location, shown by its title from the run. */
function FieldRow({ row, title }: { row: ComparedField; title: string | undefined }) {
  return (
    <tr data-result={row.result}>
      <td>
        {row.field ? titleCase(row.field) : (title ?? row.location)}
        <span className="hint compare-source">
          {row.field ? `${row.location} · ${row.connector}` : row.connector}
        </span>
      </td>
      <td>{shownValue(row, row.failing)}</td>
      <td>{shownValue(row, row.healthy)}</td>
      <td>
        <span className="compare-result" data-result={row.result}>
          {RESULT_TITLES[row.result]}
        </span>
      </td>
    </tr>
  );
}

/** Why there is nothing to compare, or that the search is still on. */
function AttemptNote({ attempt }: { attempt: HealthyAttempt }) {
  if (attempt.status === "running") {
    return (
      <p className="hint" role="status" aria-busy="true">
        {attempt.pasted ? "Running the probe on that ID…" : "Looking for a healthy record: up to 3 runs of this probe…"}
      </p>
    );
  }
  if (attempt.status === "compared" || !attempt.message) return null;
  if (attempt.status === "could_not_run") {
    return (
      <p className="error-state" role="alert">
        The comparison could not run: {attempt.message}
      </p>
    );
  }
  return <p className="summary" role="status">{attempt.message}</p>;
}

/** Compare a finished run with a known-good subject: the same probe, side by side. */
function Comparison({ runId, production }: { runId: string; production: boolean }) {
  const query = useHealthyComparison(runId);
  const failing = useRun(runId);
  const titles = new Map((failing.data?.locations ?? []).map((l) => [l.location, l.title]));
  const [failingPasted, setFailingPasted] = useState("");
  const start = useStartHealthyComparison(runId, failingPasted || undefined);
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const [showAll, setShowAll] = useState(false);

  if (query.isPending) return <p aria-busy="true">Loading the comparison…</p>;
  if (query.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{query.error.message}</p>
        <button className="btn" type="button" onClick={() => void query.refetch()}>
          Retry
        </button>
      </div>
    );
  }

  const found = query.data;
  const attempt = found.attempt ?? undefined;
  const running = attempt?.status === "running" || start.isPending;
  // This run's key is PII and stored masked: finding a healthy record by itself needs its ID.
  const masked = found.failing_subject === "[masked]";
  const needsId = masked && failingPasted === "";
  const compared = attempt?.status === "compared";
  const { highlighted, hidden, matching, notCompared } = partDifferences(found.differences ?? []);
  const differing = highlighted.filter((row) => row.result === "different").length;
  const tableShown = showAll || highlighted.length > 0;

  function paste(event: FormEvent) {
    event.preventDefault();
    start.mutate({ subject: pasted.trim() });
  }

  return (
    <>
      <header className="compare-header">
        <div>
          <h2>Compare with a healthy record</h2>
          <p className="hint">{subtitle(found, production)}</p>
        </div>
        <div className="compare-choices" role="group" aria-label="Find the healthy record by">
          {found.has_sample && (
            <button type="button" className="filter-chip" disabled={running || needsId} aria-pressed={attempt !== undefined && !attempt.narrow && !attempt.pasted} onClick={() => start.mutate({})}>
              Newest passing
            </button>
          )}
          {(found.choices ?? []).map((choice) => (
            <button key={choice.name} type="button" className="filter-chip" disabled={running || needsId} aria-pressed={attempt?.narrow === choice.name} onClick={() => start.mutate({ narrow: choice.name })}>
              {choice.title}
            </button>
          ))}
          <button type="button" className="filter-chip" disabled={running} aria-pressed={pasting || attempt?.pasted === true} onClick={() => setPasting((open) => !open)}>
            Paste an ID
          </button>
        </div>
      </header>

      {masked && (found.has_sample || (found.choices ?? []).length > 0) && (
        <label className="runbook-pasted">
          This run's key is PII and stored masked: paste its ID to find a healthy record by itself
          <input value={failingPasted} autoComplete="off" spellCheck={false} onChange={(e) => setFailingPasted(e.target.value.trim())} />
        </label>
      )}

      {pasting && (
        <form className="compare-paste" onSubmit={paste}>
          <label>
            Known-good ID <input value={pasted} onChange={(e) => setPasted(e.target.value)} />
          </label>
          <button className="btn" type="submit" disabled={running || pasted.trim() === ""}>
            Compare
          </button>
        </form>
      )}

      {start.isError && (
        <p className="error-state" role="alert">
          {start.error.message} Choose above to try again.
        </p>
      )}
      {attempt?.replaced && (
        <p className="hint compare-replaced">
          {attempt.replaced_subject
            ? `This replaced the earlier comparison with ${attempt.replaced_subject}. Its runs stay in the audit.`
            : "This replaced an earlier attempt, which compared nothing. Its runs stay in the audit."}
        </p>
      )}
      {attempt === undefined && !start.isPending && (
        <p className="hint">{found.has_sample ? "Choose how to find a healthy record." : "This entity has no healthy sample: paste a known-good ID."}</p>
      )}
      {attempt && <AttemptNote attempt={attempt} />}
      {found.warning && <p className="summary compare-warning" role="alert">{found.warning}</p>}

      {compared && (
        <>
          {!tableShown && <p className="summary" role="status">{nothingHighlighted(matching, notCompared)}</p>}
          {tableShown && <div className="table-scroll">
            <table className="compare-fields">
              <thead>
                <tr>
                  <th scope="col">Location or field</th>
                  <th scope="col">Failing {found.failing_subject}</th>
                  <th scope="col">{found.warning ? "Did not pass" : "Healthy"} {found.healthy_subject}</th>
                  <th scope="col">Difference</th>
                </tr>
              </thead>
              <tbody>
                {highlighted.map((row) => <FieldRow key={`${row.location}/${row.field ?? ""}`} row={row} title={titles.get(row.location)} />)}
                {showAll && hidden.map((row) => <FieldRow key={`${row.location}/${row.field ?? ""}`} row={row} title={titles.get(row.location)} />)}
              </tbody>
            </table>
          </div>}
          <footer className="compare-footer">
            <p className="hint">
              {differing} {differing === 1 ? "difference" : "differences"} · {matching} matching · {notCompared} not compared · fields come from the table catalog's marked columns and each REST location's compare list
            </p>
            {hidden.length > 0 && (
              <button type="button" className="btn btn-outline" onClick={() => setShowAll((all) => !all)}>
                {showAll ? "Hide fields" : "Show all fields"}
              </button>
            )}
          </footer>
        </>
      )}
    </>
  );
}

/** Shown under a finished run with one subject; a run with none has nothing to compare. */
export function HealthyComparisonSection({ runId, production }: { runId: string; production: boolean }) {
  const run = useRun(runId);
  const data = run.data;
  if (data === undefined || data.subject == null || (data.status !== "passed" && data.status !== "failed")) return null;
  return (
    <section className="card healthy-comparison" aria-label="Compare with a healthy record">
      <Comparison runId={runId} production={production} />
    </section>
  );
}
