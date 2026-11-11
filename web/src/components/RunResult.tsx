// Author: @SaadZarook
import { useState } from "react";
import type { ExpectationEvaluation, LocationResult, Run } from "../api/client";
import { useEntity } from "../api/useEntities";
import { useRun } from "../api/useRun";
import { REASON_LABELS } from "../reasons";
import { RESULT_LABELS, formatTime, keyLine, titleCase, withCaveat } from "../runFormat";
import { useSelection } from "../selection";
import { CliCommand } from "./CliCommand";
import { EvidencePanel } from "./EvidencePanel";
import { OutcomeChip } from "./OutcomeChip";
import { RunWarnings } from "./RunWarnings";
import { StopReason } from "./StopReason";

function countOf(run: Run, result: ExpectationEvaluation["result"]): number {
  return (run.expectations ?? []).filter((e) => e.result === result).length;
}

/** Where the dependency order first breaks: the first location that was not Found, else the first failed expectation. */
function firstFailure(run: Run): string | undefined {
  const location = (run.locations ?? []).find((l) => l.outcome !== "found");
  if (location) {
    const reason = location.reason ? `: ${location.reason}` : "";
    return `${location.title} ${withCaveat(location.outcome.replaceAll("_", " "), location.caveat)}${reason}`;
  }
  const failed = (run.expectations ?? []).find((e) => e.result === "fail");
  return failed ? `${failed.expectation}${failed.detail ? `: ${failed.detail}` : ""}` : undefined;
}

/** A run as it progresses and once finished: header, locations, expectations and evidence. */
export function RunResult({ runId }: { runId: string }) {
  const { application } = useSelection();
  const query = useRun(runId);
  // For where each location's key came from (key_from); the line still shows its key without it.
  const entity = useEntity(query.data?.application, query.data?.entity);
  const [selected, setSelected] = useState<number>();

  if (query.isPending) return <p aria-busy="true">Loading run…</p>;
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

  const run = query.data;
  const locations = run.locations ?? [];
  const expectations = run.expectations ?? [];
  const running = run.status === "running";
  // A stopped run judged nothing, so it has no first failure to report.
  const failure = run.status === "failed" && !run.stop_reason ? firstFailure(run) : undefined;
  const firstBad = locations.findIndex((l) => l.outcome !== "found");
  const current: LocationResult | undefined = locations[selected ?? (firstBad >= 0 ? firstBad : 0)];
  const environment = application?.environments.find((e) => e.name === run.environment);
  const record = run.investigation;

  return (
    <section className="card run-result" aria-busy={running} aria-label="Run result">
      <header className="run-header">
        <span className="status" data-status={run.status} data-caveat={run.caveat ? true : undefined}>
          {withCaveat(titleCase(run.status), run.caveat)}
        </span>
        <StopReason run={run} />
        <div>
          <h2>
            {run.probe_title} on {run.identifier_value}
          </h2>
          <p className="hint">
            Looked up as <code>{run.identifier}</code>
            {run.subject && run.subject !== run.identifier_value ? (
              <>
                , subject <code>{run.subject}</code>
              </>
            ) : null}
          </p>
          <CliCommand run={run} />
          <p className="hint">
            Run {run.id} · {formatTime(run.started_at)} ·{" "}
            {running ? "running…" : `${countOf(run, "fail")} failed, ${countOf(run, "pass")} passed, ${countOf(run, "skip")} skipped`}
          </p>
          {record && (
            <p className="hint">
              <span data-production={environment?.production === true}>{environment?.title ?? run.environment}</span> ·{" "}
              {REASON_LABELS[record.reason]}
              {record.reference ? ` · ${record.reference}` : ""} · by {run.actor.name}
            </p>
          )}
        </div>
      </header>

      <RunWarnings run={run} />
      {run.status === "skipped" && run.skip_reason && <p className="summary">Skipped: {run.skip_reason}</p>}
      {failure && <p className="summary failure">First failure in dependency order: {failure}</p>}

      <div className="run-body">
        <div>
          <h3>Locations</h3>
          {locations.length === 0 && <p className="hint">{running ? "Waiting for the first location…" : "No locations."}</p>}
          <ul className="locations">
            {locations.map((l, index) => (
              <li key={`${l.location}:${l.row ?? ""}`}>
                <button type="button" aria-pressed={l === current} onClick={() => setSelected(index)}>
                  <span className="location-name">
                    {l.title}
                    {l.evidence && <span className="hint"> {l.evidence.connector}</span>}
                  </span>
                  <OutcomeChip outcome={l.outcome} caveat={l.caveat} />
                  <code className="key-line">{keyLine(l, entity.data)}</code>
                  {l.evidence && <span className="hint duration">{l.evidence.duration_ms} ms</span>}
                  {l.reason && <span className="hint location-reason">{l.reason}</span>}
                </button>
              </li>
            ))}
          </ul>
          {running && <p className="hint">Locations are filling in…</p>}
        </div>
        <div>
          <h3>Expectations</h3>
          {expectations.length === 0 && <p className="hint">{running ? "Expectations follow the locations." : "No expectations."}</p>}
          <ul className="expectations">
            {expectations.map((e) => (
              <li key={`${e.step}:${e.expectation}`}>
                <span className="result" data-result={e.result}>
                  {RESULT_LABELS[e.result]}
                </span>
                <code>{e.expectation}</code>
                {e.detail && <span className="hint">{e.detail}</span>}
                {e.caveat && <span className="hint caveat">{withCaveat(RESULT_LABELS[e.result], e.caveat)}</span>}
              </li>
            ))}
          </ul>
          {/* Evidence names the location by its id: the expressions use it. */}
          {current && <EvidencePanel title={current.location} location={current} />}
        </div>
      </div>
    </section>
  );
}
