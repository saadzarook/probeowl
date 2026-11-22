// Author: @SaadZarook
import { Button, Field, Input, Select } from "../ui";
import { RunStatusChip } from "./RunStatusChip";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { DraftRefusedError, PasswordMissingError, startDraftRun, type Reason } from "../api/client";
import { useRun } from "../api/useRun";
import { REASON_LABELS, REFERENCE_PLACEHOLDER } from "../reasons";
import { titleCase, withCaveat } from "../runFormat";
import { useSelection } from "../selection";
import { RunNeedsPassword } from "./RunNeedsPassword";

interface TestRunBoxProps {
  probe: string;
  /** The entity YAML, only when it was edited. */
  entity: string | undefined;
  /** The entity's root key, named in the empty ID field; never another entity's example ID. */
  rootKey?: string | undefined;
}

function Finished({ runId }: { runId: string }) {
  const query = useRun(runId);
  if (query.isPending) return <p aria-busy="true">Running the draft…</p>;
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
  const expectations = run.expectations ?? [];
  const passed = expectations.filter((e) => e.result === "pass").length;
  return (
    <div className="test-run-result" aria-live="polite">
      <RunStatusChip status={run.status} caveat={Boolean(run.caveat)}>
        {withCaveat(titleCase(run.status), run.caveat)}
      </RunStatusChip>
      <span>
        {passed} of {expectations.length} expectations
      </span>
      <Link to={`/runs?run=${run.id}`}>Open run</Link>
      <p className="hint">{run.probe_version}</p>
    </div>
  );
}

/** Run the draft on one subject: an ID and the investigation record, then follow the run. */
export function TestRunBox({ probe, entity, rootKey }: TestRunBoxProps) {
  const { application, environment } = useSelection();
  const [value, setValue] = useState("");
  const [reason, setReason] = useState<Reason | "">("");
  const [reference, setReference] = useState("");
  const [started, setStarted] = useState<string>();
  // The application travels with the request, so a run again goes where the first was sent.
  const start = useMutation({
    mutationFn: ({ application, body }: { application: string; body: Parameters<typeof startDraftRun>[1] }) => startDraftRun(application, body),
    onSuccess: setStarted,
  });

  const production = environment?.production === true;
  const ready = application !== undefined && environment !== undefined && value.trim() !== "" && reason !== "" && (!production || reference.trim() !== "");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setStarted(undefined);
    start.mutate({
      application: application.name,
      body: {
        probe,
        entity,
        environment: environment.name,
        value: value.trim(),
        investigation: { reason, reference: reference.trim() || undefined },
      },
    });
  }

  const refused = start.error instanceof DraftRefusedError ? start.error : undefined;

  // The prompt is a form of its own, so it sits beside this one: forms never nest.
  return (
    <>
      <form className="card test-run flex flex-col gap-2" onSubmit={submit} aria-label="Test run">
        <h2>Test run</h2>
        <Field id="test-run-value" label="ID">
          <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder={rootKey ? `a ${rootKey} or another identifier` : ""} />
        </Field>
        <Field id="test-run-reason" label="Reason">
          <Select value={reason} onChange={(e) => setReason(e.target.value as Reason | "")}>
            <option value="">Select a reason</option>
            {Object.entries(REASON_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="test-run-reference" label={`Reference${production ? "" : " (optional)"}`}>
          <Input value={reference} onChange={(e) => setReference(e.target.value)} placeholder={REFERENCE_PLACEHOLDER} />
        </Field>
        <div>
          <Button
            variant="outline"
            type="submit"
            disabled={!ready || start.isPending}
            disabledReason={start.isPending ? undefined : runMissing(value, reason, production && reference.trim() === "")}
          >
            {start.isPending ? "Starting…" : "Run draft"}
          </Button>
        </div>
        {refused && (
          <div role="alert" className="form-error">
            <p>{refused.message}</p>
            <ul>
              {refused.failures.map((f) => (
                <li key={f.field}>
                  {f.field}: {f.message}
                </li>
              ))}
            </ul>
            <p>See the Save checks panel.</p>
          </div>
        )}
        {start.isError && !refused && !(start.error instanceof PasswordMissingError) && (
          <p role="alert" className="form-error">
            {start.error.message}
          </p>
        )}
        {started && <Finished runId={started} />}
      </form>
      {application && (
        <RunNeedsPassword
          error={start.error}
          request={start.variables && { application: start.variables.application, environment: start.variables.body.environment }}
          onRerun={() => start.variables && start.mutate(start.variables)}
        />
      )}
    </>
  );
}

/** Why Run draft can't be pressed yet (rule 8): a reference only in Production. */
function runMissing(value: string, reason: string, needsReference: boolean): string | undefined {
  const missing = [value.trim() === "" && "an ID", reason === "" && "a reason", needsReference && "a reference"].filter(
    (part): part is string => typeof part === "string",
  );
  return missing.length > 0 ? `To run: ${missing.join(", ")}.` : undefined;
}
