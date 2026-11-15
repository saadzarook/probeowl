// Author: @SaadZarook
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import type { Entity, Reason, StartRun } from "../api/client";
import { useProbes } from "../api/useProbes";
import { detectIdentifier } from "../identifiers";
import { REASON_LABELS, REFERENCE_PLACEHOLDER } from "../reasons";
import { useSelection } from "../selection";
import { Button, Card, Field, FormGrid, Input, Select } from "../ui";
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

  // ?value= from search fills the field; nothing runs until the person presses the button.
  const [params] = useSearchParams();
  const [value, setValue] = useState(() => params.get("value") ?? "");
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

  const hint =
    value.trim() === "" ? (
      <>Accepts {others.join(", ")}</>
    ) : chosen ? (
      <>
        Looked up as <strong>{chosen}</strong>
      </>
    ) : detected ? (
      <>
        Detected <strong>{detected}</strong>
        {others.length > 0 && <> · also accepts {others.join(", ")}</>}
      </>
    ) : (
      <>No pattern places this value: each identifier is tried, {entity.root_key} first.</>
    );
  // Why Inspect can't be pressed yet, next to it (rule 8).
  const missing = [
    value.trim() === "" && "an identifier",
    reason === "" && "a reason",
    production && reference.trim() === "" && "a reference",
    probe === undefined && "a probe",
  ].filter((part): part is string => typeof part === "string");

  return (
    <Card className="mb-6">
      <form aria-label="Inspect" onSubmit={submit} className="flex flex-col gap-4">
        <FormGrid>
          <Field id="inspect-environment" label="Environment">
            <Select
              tone={production ? "production" : undefined}
              value={environment?.name ?? ""}
              onChange={(e) => selectEnvironment(e.target.value)}
            >
              {application?.environments.map((choice) => (
                <option key={choice.name} value={choice.name}>
                  {choice.title}
                </option>
              ))}
            </Select>
          </Field>
          {/* The hint follows what is typed, so it is announced as it changes. */}
          <Field id="inspect-identifier" label="Identifier" help={<span aria-live="polite">{hint}</span>} error={valueError}>
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={`a ${entity.root_key} or another identifier`}
              required
            />
          </Field>
          <Field id="inspect-find-by" label="Find by">
            <Select value={chosen} onChange={(e) => setChosen(e.target.value)}>
              <option value="">Detect automatically</option>
              {Object.keys(entity.identifiers ?? {}).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
          {/* The id, as the CLI and the probe's file name it; the select already names the probe. */}
          <Field id="inspect-probe" label="Probe" help={probe && <code aria-hidden="true" className="font-mono">{probe}</code>}>
            <Select value={probe ?? ""} onChange={(e) => setProbeChoice(e.target.value)}>
              {inspectProbes.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.title}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="inspect-reason" label="Reason">
            <Select value={reason} onChange={(e) => setReason(e.target.value as Reason | "")} required>
              <option value="">Select a reason</option>
              {Object.entries(REASON_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="inspect-reference" label={`Reference${production ? "" : " (optional)"}`}>
            <Input value={reference} onChange={(e) => setReference(e.target.value)} placeholder={REFERENCE_PLACEHOLDER} required={production} />
          </Field>
        </FormGrid>
        <p className="m-0 text-sm text-muted">
          Reason is always required; Reference is required in Production. Investigator, environment, time and probe version
          are recorded automatically.
        </p>
        {probes.isError && (
          <p role="alert" className="m-0 text-sm text-production-text">
            {probes.error.message}
          </p>
        )}
        {inspectProbes.length === 0 && probes.isSuccess && (
          <NoProbes kind="inspect" application={application?.title ?? "this application"} entity={entity.entity} />
        )}
        {error && (
          <p role="alert" className="m-0 text-sm text-production-text">
            {error}
          </p>
        )}
        <div>
          <Button
            type="submit"
            disabled={!ready || pending}
            disabledReason={pending ? undefined : missing.length > 0 ? `To inspect: ${missing.join(", ")}.` : undefined}
          >
            {pending ? "Starting…" : "Inspect"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
