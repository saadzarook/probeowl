// Author: @SaadZarook
import { useState, type FormEvent } from "react";
import type { Entity, Reason, StartRun } from "../api/client";
import { useProbes } from "../api/useProbes";
import { detectIdentifier } from "../identifiers";
import { REASON_LABELS } from "../reasons";
import { useSelection } from "../selection";
import { NoProbes } from "./NoProbes";

interface InspectFormProps {
  entity: Entity;
  pending: boolean;
  error: string | undefined;
  /** What the server refused about the value typed, shown at the field. */
  valueError?: string;
  onSubmit: (request: StartRun) => void;
}

/** The Inspect form: one identifier box with its detected type, the probe, environment and investigation record. */
export function InspectForm({ entity, pending, error, valueError, onSubmit }: InspectFormProps) {
  const { application, environment, selectEnvironment } = useSelection();
  const probes = useProbes(application?.name, entity.entity);
  const inspectProbes = probes.data?.filter((p) => p.kind === "inspect") ?? [];
  const [probeChoice, setProbeChoice] = useState<string>();
  const probe = inspectProbes.find((p) => p.name === probeChoice)?.name ?? inspectProbes[0]?.name;

  const [value, setValue] = useState("");
  // "" lets the server place the value: by pattern, or by trying each identifier in turn.
  const [chosen, setChosen] = useState("");
  const [reason, setReason] = useState<Reason | "">("");
  const [reference, setReference] = useState("");

  const production = environment?.production === true;
  const detected = detectIdentifier(entity, value);
  const others = Object.keys(entity.identifiers ?? {}).filter((name) => name !== detected);
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
    onSubmit({
      application: application.name,
      environment: environment.name,
      probe,
      value: value.trim(),
      ...(chosen ? { identifier: chosen } : {}),
      investigation: { reason, reference: reference.trim() || undefined },
    });
  }

  return (
    <form className="card validate-form" onSubmit={submit}>
      <div className="form-row inspect-row">
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
        <div className="identifier-field">
          <label>
            Identifier
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={`a ${entity.root_key} or another identifier`}
              aria-describedby={valueError ? "identifier-error identifier-hint" : "identifier-hint"}
              aria-invalid={valueError ? true : undefined}
              required
            />
          </label>
          <p className="hint" id="identifier-hint" aria-live="polite">
            {value.trim() === "" ? (
              <>Accepts {others.join(", ")}</>
            ) : chosen ? (
              <>Looked up as <strong>{chosen}</strong></>
            ) : detected ? (
              <>
                Detected <strong>{detected}</strong>
                {others.length > 0 && <> · also accepts {others.join(", ")}</>}
              </>
            ) : (
              <>No pattern places this value: each identifier is tried, {entity.root_key} first.</>
            )}
          </p>
          <label className="find-by">
            Find by
            <select value={chosen} onChange={(e) => setChosen(e.target.value)}>
              <option value="">Detect automatically</option>
              {Object.keys(entity.identifiers ?? {}).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {valueError && (
            <p className="form-error" id="identifier-error" role="alert">
              {valueError}
            </p>
          )}
        </div>
        <label>
          Probe
          <select value={probe ?? ""} onChange={(e) => setProbeChoice(e.target.value)}>
            {inspectProbes.map((p) => (
              <option key={p.name} value={p.name}>
                {p.title}
              </option>
            ))}
          </select>
          {/* The id, as the CLI and the probe's file name it; the select already names the probe. */}
          {probe && <span className="field-id" aria-hidden="true">{probe}</span>}
        </label>
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
          <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="SUP-20931" required={production} />
        </label>
        <button className="btn" type="submit" disabled={!ready || pending}>
          {pending ? "Starting…" : "Inspect"}
        </button>
      </div>
      <p className="hint">
        Reason is always required; Reference is required in Production. Investigator, environment, time and probe version
        are recorded automatically.
      </p>
      {probes.isError && (
        <p role="alert" className="form-error">
          {probes.error.message}
        </p>
      )}
      {inspectProbes.length === 0 && probes.isSuccess && (
        <NoProbes kind="inspect" application={application?.title ?? "this application"} entity={entity.entity} />
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </form>
  );
}
