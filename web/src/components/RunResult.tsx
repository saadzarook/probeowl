// Author: @SaadZarook
import { Button } from "../ui";
import { RunStatusChip } from "./RunStatusChip";
import { useState } from "react";
import type { ExpectationEvaluation, LocationResult, Run } from "../api/client";
import { useEntity } from "../api/useEntities";
import { useRun } from "../api/useRun";
import { REASON_LABELS } from "../reasons";
import { RESULT_LABELS, formatTime, keyLine, locationName, titleCase, withCaveat } from "../runFormat";
import { ResultChip } from "./ResultChip";
import { useSelection } from "../selection";
import { CliCommand } from "./CliCommand";
import { EvidencePanel } from "./EvidencePanel";
import { NotChecked } from "./NotChecked";
import { OutcomeChip } from "./OutcomeChip";
import { NotApplicableNote } from "./NotApplicableNote";
import { RunWarnings } from "./RunWarnings";
import { StopReason } from "./StopReason";

function countOf(run: Run, result: ExpectationEvaluation["result"]): number {
  // A check not applicable here is counted on its own, not among the skipped (#25).
  return (run.expectations ?? []).filter((e) => e.result === result && e.not_applicable !== true).length;
}

function notApplicableCount(run: Run): number {
  return (run.expectations ?? []).filter((e) => e.not_applicable === true).length;
}

// Locations whose data could not be read: listed apart from what failed (#82).
const UNREAD: ReadonlySet<LocationResult["outcome"]> = new Set(["error", "stale", "not_configured", "waiting", "blocked"]);

/** What to lead with: the first failed expectation, else where the dependency order first breaks. */
function firstFailure(run: Run): { failed?: string; order?: string } {
  const failed = (run.expectations ?? []).find((e) => e.result === "fail");
  // With its caveat, such as a Missing record on a replica of unknown lag: never shown as certain.
  if (failed) return { failed: withCaveat(`${failed.expectation}${failed.detail ? `: ${failed.detail}` : ""}`, failed.caveat) };
  const location = (run.locations ?? []).find((l) => l.outcome !== "found");
  if (!location) return {};
  const reason = location.reason ? `: ${location.reason}` : "";
  return { order: `${locationName(location)} ${withCaveat(location.outcome.replaceAll("_", " "), location.caveat)}${reason}` };
}

/** The locations that could not be read, each named (a fan-out row by its key) with its reason. */
function unreadLocations(run: Run): string[] {
  return (run.locations ?? [])
    .filter((l) => UNREAD.has(l.outcome))
    .map((l) => `${locationName(l)}: ${l.outcome.replaceAll("_", " ")}${l.reason ? `, ${l.reason}` : ""}`);
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
        <Button type="button" onClick={() => void query.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const run = query.data;
  const locations = run.locations ?? [];
  const expectations = run.expectations ?? [];
  const running = run.status === "running";
  // A stopped run judged nothing, so it has no first failure to report.
  const failure = run.status === "failed" && !run.stop_reason ? firstFailure(run) : {};
  const unread = failure.failed ? unreadLocations(run) : [];
  const firstBad = locations.findIndex((l) => l.outcome !== "found");
  const current: LocationResult | undefined = locations[selected ?? (firstBad >= 0 ? firstBad : 0)];
  const environment = application?.environments.find((e) => e.name === run.environment);
  const record = run.investigation;

  return (
    <section className="card run-result" aria-busy={running} aria-label="Run result">
      <header className="run-header">
        <RunStatusChip status={run.status} caveat={Boolean(run.caveat)}>
          {withCaveat(titleCase(run.status), run.caveat)}
        </RunStatusChip>
        <StopReason run={run} />
        <div>
          <h2>
            {run.probe_title} on <code className="font-mono">{run.identifier_value}</code>
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
            {running ? "running…" : `${countOf(run, "fail")} failed, ${countOf(run, "pass")} passed, ${countOf(run, "skip")} skipped${notApplicableCount(run) > 0 ? `, ${notApplicableCount(run)} not applicable` : ""}`}
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
      <NotApplicableNote run={run} environment={environment?.title ?? run.environment} />
      {run.status === "skipped" && run.skip_reason && <p className="summary">Skipped: {run.skip_reason}</p>}
      {failure.failed && <p className="summary failure">First failed expectation: {failure.failed}</p>}
      {unread.length > 0 && (
        <div className="summary" role="note">
          <p>Not read, so not part of that failure:</p>
          <ul>
            {unread.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      {failure.order && <p className="summary failure">First failure in dependency order: {failure.order}</p>}
      <NotChecked run={run} />

      <div className="run-body">
        <div>
          <h3>Locations</h3>
          {locations.length === 0 && <p className="hint">{running ? "Waiting for the first location…" : "No locations."}</p>}
          <ul className="locations">
            {locations.map((l, index) => (
              <li key={`${l.location}:${l.row ?? ""}`}>
                <button type="button" aria-pressed={l === current} onClick={() => setSelected(index)}>
                  <span className="location-name">
                    {locationName(l)}
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
                <ResultChip evaluation={e} />
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
