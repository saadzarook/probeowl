// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { PasswordMissingError, splitError, startRun, type Reason } from "../api/client";
import { useEntities } from "../api/useEntities";
import { useProbes } from "../api/useProbes";
import { REASON_LABELS } from "../reasons";
import { useSelection } from "../selection";
import { NoProbes } from "./NoProbes";
import { RunNeedsPassword } from "./RunNeedsPassword";

interface ValidateFormProps {
  onStarted: (id: string) => void;
}

/** The Validate form: what to check, on which subject, and the investigation record. */
export function ValidateForm({ onStarted }: ValidateFormProps) {
  const { application, environment, selectEnvironment } = useSelection();
  const entities = useEntities(application?.name);
  const [entityChoice, setEntityChoice] = useState<string>();
  const entity = entities.data?.find((e) => e.entity === entityChoice)?.entity ?? entities.data?.[0]?.entity;
  const probes = useProbes(application?.name, entity);
  const validateProbes = probes.data?.filter((p) => p.kind === "validate") ?? [];
  const [probeChoice, setProbeChoice] = useState<string>();
  const probe = validateProbes.find((p) => p.name === probeChoice)?.name ?? validateProbes[0]?.name;

  const [value, setValue] = useState("");
  // "" lets the server place the value: by pattern, or by trying each identifier in turn.
  const [chosen, setChosen] = useState("");
  const identifiers = Object.keys(entities.data?.find((e) => e.entity === entity)?.identifiers ?? {});
  const [reason, setReason] = useState<Reason | "">("");
  const [reference, setReference] = useState("");
  const [customerReference, setCustomerReference] = useState("");
  const [notes, setNotes] = useState("");

  const start = useMutation({ mutationFn: startRun, onSuccess: onStarted });
  const failed = splitError(start.error, "value");

  const production = environment?.production === true;
  const ready =
    application !== undefined &&
    environment !== undefined &&
    probe !== undefined &&
    value.trim() !== "" &&
    reason !== "" &&
    (!production || reference.trim() !== "");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    start.mutate({
      application: application.name,
      environment: environment.name,
      probe,
      value: value.trim(),
      ...(chosen && identifiers.includes(chosen) ? { identifier: chosen } : {}),
      investigation: {
        reason,
        reference: reference.trim() || undefined,
        notes: notes.trim() || undefined,
        customer_reference: customerReference.trim() || undefined,
      },
    });
  }

  if (entities.isPending) return <p aria-busy="true">Loading entities…</p>;
  if (entities.isError) {
    return (
      <div className="error-state" role="alert">
        <p>{entities.error.message}</p>
        <button className="btn" type="button" onClick={() => void entities.refetch()}>
          Retry
        </button>
      </div>
    );
  }

  // The prompt is a form of its own, so it sits beside this one: forms never nest.
  return (
    <>
      <form className="card validate-form" onSubmit={submit}>
        <div className="form-row">
          <label>
            Environment
            <select
              className="env-select"
              data-production={production}
              value={environment?.name ?? ""}
              onChange={(e) => selectEnvironment(e.target.value)}
            >
              {application?.environments.map((choice) => (
                <option key={choice.name} value={choice.name}>
                  {choice.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Entity
            <select value={entity ?? ""} onChange={(e) => setEntityChoice(e.target.value)}>
              {entities.data.map((e) => (
                <option key={e.entity} value={e.entity}>
                  {e.title}
                </option>
              ))}
            </select>
          </label>
          <div className="identifier-field">
            <label>
              Any ID
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={identifiers[0] ?? ""}
                aria-describedby={failed.field ? "value-error" : undefined}
                aria-invalid={failed.field ? true : undefined}
                required
              />
            </label>
            <label className="find-by">
              Find by
              <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
                <option value="">Detect automatically</option>
                {identifiers.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            {failed.field && (
              <p className="form-error" id="value-error" role="alert">
                {failed.field}
              </p>
            )}
          </div>
          <label>
            Probe
            <select value={probe ?? ""} onChange={(e) => setProbeChoice(e.target.value)}>
              {validateProbes.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.title}
                </option>
              ))}
            </select>
            {/* The id, as the CLI and the probe's file name it; the select already names the probe. */}
            {probe && <span className="field-id" aria-hidden="true">{probe}</span>}
          </label>
        </div>
        <p className="hint">Accepts any identifier of the entity; the key graph resolves the rest.</p>
        {probes.isError && (
          <p role="alert" className="form-error">
            {probes.error.message}
          </p>
        )}
        {validateProbes.length === 0 && probes.isSuccess && (
          <NoProbes kind="validate" application={application?.title ?? "this application"} entity={entity} />
        )}

        <fieldset className="record">
          <legend>
            Investigation record <span className="hint">saved with the run for audit</span>
          </legend>
          <div className="form-row">
            <label>
              Reason
              <select value={reason} onChange={(e) => setReason(e.target.value as Reason | "")} required>
                <option value="">Select a reason</option>
                {Object.entries(REASON_LABELS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Reference{production ? "" : " (optional)"}
              <input
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="WALLET-482"
                required={production}
              />
            </label>
            <label>
              Customer reference (optional)
              <input
                value={customerReference}
                onChange={(e) => setCustomerReference(e.target.value)}
                placeholder="Stored masked"
              />
            </label>
            <label>
              Notes (optional)
              <input value={notes} onChange={(e) => setNotes(e.target.value)} />
            </label>
          </div>
          <p className="hint">
            Reason is always required; Reference is required in Production. Investigator, environment, time and probe
            version are recorded automatically.
          </p>
        </fieldset>

        {failed.rest && !(start.error instanceof PasswordMissingError) && (
          <p role="alert" className="form-error">
            {failed.rest}
          </p>
        )}
        <button className="btn" type="submit" disabled={!ready || start.isPending}>
          {start.isPending ? "Starting…" : "Validate"}
        </button>
      </form>
      {application && (
        <RunNeedsPassword
          error={start.error}
          request={start.variables}
          onRerun={() => start.variables && start.mutate(start.variables)}
        />
      )}
    </>
  );
}
