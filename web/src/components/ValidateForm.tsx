// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { PasswordMissingError, splitError, startRun, type Reason } from "../api/client";
import { useEntities } from "../api/useEntities";
import { useProbes } from "../api/useProbes";
import { REASON_LABELS, REFERENCE_PLACEHOLDER } from "../reasons";
import { useSelection } from "../selection";
import { Button, Card, Field, FormGrid, Input, Select } from "../ui";
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

  // ?value= from search fills the field; nothing runs until the person presses the button.
  const [params] = useSearchParams();
  const [value, setValue] = useState(() => params.get("value") ?? "");
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
        <Button type="button" onClick={() => void entities.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  // Why Validate can't be pressed yet, next to it (rule 8).
  const missing = [
    value.trim() === "" && "an ID",
    reason === "" && "a reason",
    production && reference.trim() === "" && "a reference",
    probe === undefined && "a probe",
  ].filter((part): part is string => typeof part === "string");

  // The prompt is a form of its own, so it sits beside this one: forms never nest.
  return (
    <>
      <Card className="mb-6 flex flex-col gap-4">
        <form aria-label="Validate" onSubmit={submit} className="flex flex-col gap-4">
          <FormGrid>
            <Field id="validate-environment" label="Environment">
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
            <Field id="validate-entity" label="Entity">
              <Select value={entity ?? ""} onChange={(e) => setEntityChoice(e.target.value)}>
                {entities.data.map((e) => (
                  <option key={e.entity} value={e.entity}>
                    {e.title}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              id="validate-value"
              label="Any ID"
              help="Accepts any identifier of the entity; the key graph resolves the rest."
              error={failed.field}
            >
              <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder={identifiers[0] ?? ""} required />
            </Field>
            <Field id="validate-find-by" label="Find by">
              <Select value={chosen} onChange={(e) => setChosen(e.target.value)}>
                <option value="">Detect automatically</option>
                {identifiers.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            </Field>
            {/* The id, as the CLI and the probe's file name it; the select already names the probe. */}
            <Field id="validate-probe" label="Probe" help={probe && <code aria-hidden="true" className="font-mono">{probe}</code>}>
              <Select value={probe ?? ""} onChange={(e) => setProbeChoice(e.target.value)}>
                {validateProbes.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.title}
                  </option>
                ))}
              </Select>
            </Field>
          </FormGrid>
          {probes.isError && (
            <p role="alert" className="m-0 text-sm text-production-text">
              {probes.error.message}
            </p>
          )}
          {validateProbes.length === 0 && probes.isSuccess && (
            <NoProbes kind="validate" application={application?.title ?? "this application"} entity={entity} />
          )}

          <fieldset className="m-0 flex flex-col gap-2 border-0 border-t border-border p-0 pt-4">
            <legend className="float-left mb-2 w-full p-0 text-base font-semibold">
              Investigation record <span className="text-sm font-normal text-muted">saved with the run for audit</span>
            </legend>
            <FormGrid>
              <Field id="validate-reason" label="Reason">
                <Select value={reason} onChange={(e) => setReason(e.target.value as Reason | "")} required>
                  <option value="">Select a reason</option>
                  {Object.entries(REASON_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field id="validate-reference" label={`Reference${production ? "" : " (optional)"}`}>
                <Input value={reference} onChange={(e) => setReference(e.target.value)} placeholder={REFERENCE_PLACEHOLDER} required={production} />
              </Field>
              <Field id="validate-customer-reference" label="Customer reference (optional)">
                <Input value={customerReference} onChange={(e) => setCustomerReference(e.target.value)} placeholder="Stored masked" />
              </Field>
              <Field id="validate-notes" label="Notes (optional)">
                <Input value={notes} onChange={(e) => setNotes(e.target.value)} />
              </Field>
            </FormGrid>
            <p className="m-0 text-sm text-muted">
              Reason is always required; Reference is required in Production. Investigator, environment, time and probe
              version are recorded automatically.
            </p>
          </fieldset>

          {failed.rest && !(start.error instanceof PasswordMissingError) && (
            <p role="alert" className="m-0 text-sm text-production-text">
              {failed.rest}
            </p>
          )}
          <div>
            <Button
              type="submit"
              disabled={!ready || start.isPending}
              disabledReason={start.isPending ? undefined : missing.length > 0 ? `To validate: ${missing.join(", ")}.` : undefined}
            >
              {start.isPending ? "Starting…" : "Validate"}
            </Button>
          </div>
        </form>
      </Card>
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
