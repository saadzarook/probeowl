// Author: @SaadZarook
import { useState } from "react";
import type { LocationResult, Run, StepInRun } from "../api/client";
import { copyWrite, runStep } from "../api/client";
import { useRun } from "../api/useRun";
import { useRunStep, useTickStep } from "../api/useRunbooks";
import { copyText, needsPlanAcknowledgement, stepLabel, writeHeader, type RunbookStepView } from "../runbooks";
import { titleCase } from "../runFormat";
import { showValue } from "./AmbiguousMatches";
import { CopyButton } from "./CopyButton";
import { EvidencePanel } from "./EvidencePanel";
import { OutcomeChip } from "./OutcomeChip";

interface StepProps {
  application: string;
  runbook: string;
  number: number;
  step: RunbookStepView;
  /** The run its values come from; undefined when opened with no run, or with values turned off. */
  run: Run | undefined;
  /** The step in that run; undefined until loaded. */
  state: StepInRun | undefined;
  environment: string | undefined;
}

function Unfilled({ state }: { state: StepInRun | undefined }) {
  const unfilled = state?.unfilled ?? [];
  if (unfilled.length === 0) return null;
  return (
    <ul className="runbook-unfilled" aria-label="Not filled in">
      {unfilled.map((u) => (
        <li key={u.parameter}>
          <code>:{u.parameter}</code>: {u.why}
        </li>
      ))}
    </ul>
  );
}

function ReadStep({ runbook, step, run, state }: StepProps & { step: Extract<RunbookStepView, { kind: "read_query" }> }) {
  const runnable = step.runnable && run !== undefined;
  const text = state?.filled ?? step.sql;
  return (
    <>
      <p className="hint">
        {step.using}
        {step.runnable ? " · reviewed, passes its checks" : ` · not run by ProbeOwl: ${step.why_copy_only ?? "copy only"}`}
      </p>
      <pre className="runbook-code"><code>{text}</code></pre>
      <Unfilled state={state} />
      <div className="runbook-actions">
        {runnable && <RunRead run={run} runbook={runbook} step={step.id} result={state?.result ?? undefined} />}
        <CopyButton text={text} />
        {step.runnable && run === undefined && <span className="hint">Open this runbook from a run to run this step.</span>}
      </div>
    </>
  );
}

function RunRead({ run, runbook, step, result }: { run: Run; runbook: string; step: string; result: StepInRun["result"] | undefined }) {
  const running = useRunStep(run.id, runbook, step);
  const [acknowledged, setAcknowledged] = useState(false);
  const asks = running.isError && needsPlanAcknowledgement(running.error.message);
  const found: Pick<LocationResult, "evidence" | "reason"> | undefined = result ? { reason: result.reason, evidence: result.evidence } : undefined;
  return (
    <div className="runbook-run">
      <button className="btn" type="button" disabled={running.isPending || (asks && !acknowledged)} onClick={() => running.mutate(acknowledged, { onSuccess: () => setAcknowledged(false) })}>
        {running.isPending ? "Running…" : "Run read-only"}
      </button>
      {running.isError && <p className="error-state" role="alert">{running.error.message}</p>}
      {asks && (
        <label className="runbook-acknowledge">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> Run without plan check
        </label>
      )}
      {result && found && (
        <div className="runbook-result" aria-label="Result">
          <OutcomeChip outcome={result.outcome} />
          {result.reason && <span className="hint"> {result.reason}</span>}
          <span className="hint"> by {result.actor}</span>
          {(result.warnings ?? []).map((warning) => <p key={warning} className="hint">{warning}</p>)}
          <ResultRows rows={result.evidence?.rows ?? []} total={result.evidence?.rows_total ?? undefined} />
          <EvidencePanel title={titleCase(step)} location={found} />
        </div>
      )}
    </div>
  );
}

/** What a read found: its masked rows, since that is what the person ran it for. */
function ResultRows({ rows, total }: { rows: Record<string, unknown>[]; total: number | undefined }) {
  if (rows.length === 0) return null;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return (
    <div className="table-scroll runbook-rows">
      <table className="records">
        <thead>
          <tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{columns.map((column) => <td key={column}>{showValue(row[column])}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {total !== undefined && total > rows.length && <p className="hint">Showing {rows.length} of {total}</p>}
    </div>
  );
}

function WriteStep({ application, runbook, step, run, state, environment }: StepProps & { step: Extract<RunbookStepView, { kind: "write_query" }> }) {
  const [message, setMessage] = useState<{ text: string; failed: boolean }>();
  const preview = `${writeHeader(step.requires, run, environment)}\n${state?.filled ?? step.sql}`;

  /** The server records the copy first, and writes the text: a person never gets it unrecorded. */
  async function copy() {
    try {
      const copied = await copyWrite(application, runbook, step.id, run?.id);
      const done = await copyText(copied.text);
      setMessage(
        done
          ? { text: "Copied, and recorded in the audit.", failed: false }
          : { text: "Recorded in the audit, but the browser would not copy it: select the text above instead.", failed: true },
      );
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : "The copy could not be recorded.", failed: true });
    }
  }

  return (
    <>
      <p className="runbook-write-note">
        {step.relabelled ? "Labelled a read query, but it changes data, so ProbeOwl treats it as a write. " : ""}
        Requires {step.requires}. Run by a person on {step.using}. ProbeOwl has no write access and offers no run button.
      </p>
      <pre className="runbook-code"><code>{preview}</code></pre>
      <Unfilled state={state} />
      <div className="runbook-actions">
        <button className="btn btn-danger-outline" type="button" onClick={() => void copy()}>
          Copy for change request
        </button>
        {message ? (
          <span className="hint" role={message.failed ? "alert" : "status"}>{message.text}</span>
        ) : (
          <span className="hint">Copying is recorded in the audit</span>
        )}
      </div>
    </>
  );
}

function ManualStepBody({ runbook, step, run, state }: StepProps & { step: Extract<RunbookStepView, { kind: "manual" }> }) {
  return (
    <>
      <p>{step.text}</p>
      {run ? <Tick run={run} runbook={runbook} step={step.id} done={state?.done === true} /> : <p className="hint">Open this runbook from a run to tick this step.</p>}
    </>
  );
}

function Tick({ run, runbook, step, done }: { run: Run; runbook: string; step: string; done: boolean }) {
  const tick = useTickStep(run.id, runbook, step);
  // What the person chose, until the saved state has come back; a failure puts it back.
  const [chosen, setChosen] = useState<boolean>();
  function change(next: boolean) {
    setChosen(next);
    tick.mutate(next, { onSettled: () => setChosen(undefined) });
  }
  return (
    <>
      <label>
        <input type="checkbox" checked={chosen ?? done} onChange={(e) => change(e.target.checked)} /> Done · saved with run {run.id}
      </label>
      {tick.isError && <p className="error-state" role="alert">{tick.error.message}</p>}
    </>
  );
}

function ProbeStepBody({ runbook, step, run }: StepProps & { step: Extract<RunbookStepView, { kind: "probe" }> }) {
  const [started, setStarted] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const again = useRun(started);
  function start(id: string) {
    setProblem(undefined);
    runStep(id, runbook, step.id, false).then(
      (ran) => setStarted(ran.run ?? undefined),
      (error: unknown) => setProblem(error instanceof Error ? error.message : "The probe could not be started."),
    );
  }
  return (
    <>
      <p>
        <code>{step.probe}</code>
        {run?.subject ? <span className="hint"> on {run.subject}</span> : null}
      </p>
      {run && step.runnable ? (
        <button className="btn" type="button" disabled={started !== undefined && again.data?.status === "running"} onClick={() => start(run.id)}>
          {run.kind === "validate" ? "Validate again" : "Inspect again"}
        </button>
      ) : (
        <p className="hint">{step.runnable ? "Open this runbook from a run to run this probe again." : `This application has no probe named ${step.probe}.`}</p>
      )}
      {problem && <p className="error-state" role="alert">{problem}</p>}
      {started && <p className="hint" role="status">Run {started}: {again.data ? titleCase(again.data.status) : "starting…"}</p>}
    </>
  );
}

/** One step, numbered in display order. Only a read query or a probe the server marks runnable offers a run. */
export function RunbookStep(props: StepProps) {
  const { step, number } = props;
  return (
    <section className="card runbook-step" data-kind={step.kind} aria-label={`Step ${number}: ${step.title}`}>
      <header>
        <span className="runbook-number">{number}</span>
        <span className="runbook-kind" data-kind={step.kind} data-runnable={step.runnable}>{stepLabel(step)}</span>
        <h3>{step.title}</h3>
      </header>
      {step.kind === "read_query" && <ReadStep {...props} step={step} />}
      {step.kind === "write_query" && <WriteStep {...props} step={step} />}
      {step.kind === "external_query" && (
        <>
          <pre className="runbook-code"><code>{props.state?.filled ?? step.query}</code></pre>
          <Unfilled state={props.state} />
          <div className="runbook-actions">
            <CopyButton text={props.state?.filled ?? step.query} />
          </div>
        </>
      )}
      {step.kind === "manual" && <ManualStepBody {...props} step={step} />}
      {step.kind === "probe" && <ProbeStepBody {...props} step={step} />}
    </section>
  );
}
