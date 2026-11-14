// Author: @SaadZarook
import type { Entity, ExpectationEvaluation, LocationResult, Run } from "../api/client";
import { useRun } from "../api/useRun";
import { REASON_LABELS } from "../reasons";
import { RESULT_LABELS, formatTime, titleCase, withCaveat } from "../runFormat";
import { useSelection } from "../selection";
import { AmbiguousMatches, showValue } from "./AmbiguousMatches";
import { CliCommand } from "./CliCommand";
import { EvidencePanel } from "./EvidencePanel";
import { OutcomeChip } from "./OutcomeChip";
import { RunWarnings } from "./RunWarnings";
import { StopReason } from "./StopReason";

type Row = Record<string, unknown>;

function failedCount(run: Run): number {
  return (run.expectations ?? []).filter((e) => e.result === "fail").length;
}

/** "3 issues" once the expectations are in; the run status otherwise. */
function statusLabel(run: Run): string {
  if (run.status === "running") return "Running";
  const issues = failedCount(run);
  if (issues > 0) return `${issues} ${issues === 1 ? "issue" : "issues"}`;
  return run.status === "passed" ? "No issues" : titleCase(run.status);
}

/** The status label, with the run's caveat when its result may not be final. */
function statusText(run: Run): string {
  return withCaveat(statusLabel(run), run.caveat);
}

/** A "one" location: a summary card of the single record. */
function SummaryCard({ location }: { location: LocationResult }) {
  const record: Row | undefined = location.evidence?.rows?.[0];
  return (
    <div className="location-block">
      <header>
        <h3>{location.title} · one</h3>
        <OutcomeChip outcome={location.outcome} caveat={location.caveat} />
      </header>
      {record ? (
        <dl className="record-fields">
          {Object.entries(record).map(([key, value]) => (
            <div key={key}>
              <dt>{titleCase(key)}</dt>
              <dd>{showValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="hint">{location.reason ?? "No record."}</p>
      )}
    </div>
  );
}

/** A "many" location: one table row per record, with the follow-up lookups in their own columns. */
function RecordsTable({ location, followUps, searched }: { location: LocationResult; followUps: LocationResult[]; searched: string }) {
  const rows: Row[] = location.evidence?.rows ?? [];
  const total = location.evidence?.rows_total ?? rows.length;
  const columns = Object.keys(rows[0] ?? {});
  // Each fan-out location once, with its title: [controller, "Door controller"].
  const lookups = [...new Map(followUps.map((f) => [f.location, f.title])).entries()];

  return (
    <div className="location-block">
      <header>
        <h3>{location.title} · many</h3>
        <span className="hint">{total > rows.length ? `Showing ${rows.length} of ${total}` : `${rows.length} ${rows.length === 1 ? "record" : "records"}`}</span>
      </header>
      {rows.length === 0 ? (
        <p className="hint">
          <OutcomeChip outcome={location.outcome} caveat={location.caveat} /> {location.reason ?? "No records."}
        </p>
      ) : (
        <table className="records">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col">
                  {titleCase(c)}
                </th>
              ))}
              {lookups.map(([l, title]) => (
                <th key={l} scope="col">
                  {title}
                </th>
              ))}
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const own = followUps.filter((f) => f.row === index);
              const worst = own.find((f) => f.outcome !== "found");
              const note = worst?.reason ?? (Object.values(row).some((v) => v === searched) ? "searched record" : undefined);
              return (
                <tr key={index} data-problem={worst !== undefined}>
                  {columns.map((c) => (
                    <td key={c}>{showValue(row[c])}</td>
                  ))}
                  {lookups.map(([l]) => {
                    const lookup = own.find((f) => f.location === l);
                    const result: Row | undefined = lookup?.evidence?.rows?.[0];
                    return <td key={l}>{result ? Object.values(result).map(showValue).join(" · ") : lookup ? titleCase(lookup.outcome) : "—"}</td>;
                  })}
                  <td>
                    <OutcomeChip outcome={worst?.outcome ?? "found"} caveat={worst?.caveat} />
                    {note && <p className="hint">{note}</p>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Expectations({ expectations, running }: { expectations: ExpectationEvaluation[]; running: boolean }) {
  return (
    <div>
      <h3>Expectations</h3>
      {expectations.length === 0 && <p className="hint">{running ? "Expectations follow the locations." : "No expectations."}</p>}
      <ul className="expectations">
        {expectations.map((e) => (
          <li key={`${e.step}:${e.expectation}`}>
            <span className="result" data-result={e.result}>
              {RESULT_LABELS[e.result]}
            </span>
            <div>
              <code>{e.expectation}</code>
              {e.detail && <p className="hint">{e.detail}</p>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface InspectResultProps {
  runId: string;
  entity: Entity;
  /** Start runs on these subjects, picked from this run's matches. */
  onInspect: (runId: string, subjects: string[]) => void;
  /** Runs started by picking masked matches by number. */
  onStarted: (ids: string[]) => void;
}

/** An inspect run as it progresses and once finished: resolution line, locations, expectations. */
export function InspectResult({ runId, entity, onInspect, onStarted }: InspectResultProps) {
  const { application } = useSelection();
  const query = useRun(runId);

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
  const topLevel = locations.filter((l) => l.parent === undefined || l.parent === null);
  if (run.status === "ambiguous") {
    return (
      <AmbiguousMatches
        identifier={run.identifier}
        value={run.identifier_value}
        matches={run.matches ?? []}
        onInspect={(subjects) => onInspect(run.id, subjects)}
        runId={run.id}
        onStarted={onStarted}
      />
    );
  }

  const running = run.status === "running";
  const environment = application?.environments.find((e) => e.name === run.environment);
  const record = run.investigation;
  const matchCount = run.matches?.length ?? 0;

  return (
    <>
      {run.subject && run.via && (
        <p className="resolution" aria-label="Resolution">
          Resolved{" "}
          <code>
            {run.identifier} {run.identifier_value}
          </code>{" "}
          <span aria-hidden="true">→</span>{" "}
          <code>
            {entity.root_key} {run.subject}
          </code>{" "}
          <span className="hint">
            via {run.via}
            {matchCount > 0 && ` · ${matchCount} ${matchCount === 1 ? "match" : "matches"}`}
          </span>
        </p>
      )}
      <section className="card run-result" aria-busy={running} aria-label="Inspect result">
        <header className="run-header">
          <span className="status" data-status={failedCount(run) > 0 ? "failed" : run.status} data-caveat={run.caveat ? true : undefined}>
            {statusText(run)}
          </span>
          <StopReason run={run} />
          <div>
            <h2>
              {run.probe_title} on {run.subject ?? run.identifier_value}
            </h2>
            <CliCommand run={run} />
            <p className="hint">
              Run {run.id} · {formatTime(run.started_at)}
              {record && (
                <>
                  {" "}
                  · <span data-production={environment?.production === true}>{environment?.title ?? run.environment}</span> ·{" "}
                  {REASON_LABELS[record.reason]}
                  {record.reference ? ` · ${record.reference}` : ""}
                </>
              )}{" "}
              {run.from_run && <> · picked from run {run.from_run}</>} · by {run.actor.name}
            </p>
          </div>
        </header>
        <RunWarnings run={run} />
        {run.status === "skipped" && run.skip_reason && <p className="summary">Skipped: {run.skip_reason}</p>}
        <div className="run-body inspect-body">
          <div>
            {topLevel.length === 0 && <p className="hint">{running ? "Waiting for the first location…" : "No locations."}</p>}
            {topLevel.map((l) =>
              (entity.locations[l.location]?.cardinality ?? "one") === "many" ? (
                <RecordsTable
                  key={l.location}
                  location={l}
                  followUps={locations.filter((f) => f.parent === l.location)}
                  searched={run.identifier_value}
                />
              ) : (
                <SummaryCard key={l.location} location={l} />
              ),
            )}
            {running && <p className="hint">Locations are filling in…</p>}
            {topLevel
              .filter((l) => l.evidence)
              .map((l) => (
                <details key={l.location} className="evidence-details">
                  <summary>Evidence · {l.title}</summary>
                  {/* Evidence names the location by its id: the expressions use it. */}
                  <EvidencePanel title={l.location} location={l} />
                </details>
              ))}
          </div>
          <Expectations expectations={run.expectations ?? []} running={running} />
        </div>
      </section>
    </>
  );
}
