// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { saveApplication, type CatalogChange, type SaveApplication } from "../api/client";
import { useSetup } from "../api/useSetup";
import { ChangePreview } from "../components/ChangePreview";
import { GuideLink } from "../components/EmptyState";
import { useSelection } from "../selection";
import { applicationId, applicationIdProblem } from "../setupIds";

interface EnvironmentChoice {
  name: string;
  title: string;
  hint: string;
  production: boolean;
}

// Dev, UAT and Production are suggested; Staging is one click away. Only Production is marked.
const ENVIRONMENTS: EnvironmentChoice[] = [
  { name: "dev", title: "Dev", hint: "Developers' copies", production: false },
  { name: "uat", title: "UAT", hint: "Where testers check", production: false },
  { name: "staging", title: "Staging", hint: "Before release", production: false },
  { name: "prod", title: "Production", hint: "Read only from a replica", production: true },
];
const SUGGESTED = new Set(["dev", "uat", "prod"]);
const STEPS = ["Application", "Environments", "First connection"];

/** The file a new application's settings go in, under the folder owl serve was started with. */
function filePath(folder: string, id: string): string {
  const separator = folder.includes("\\") ? "\\" : "/";
  return [folder.replace(/[\\/]+$/, ""), id || "<id>", "application.yaml"].join(separator);
}

/** First run in the browser: an application, its environments, then its first connection. */
export function FirstRunPage() {
  const { applications, selectApplication } = useSelection();
  const setup = useSetup();
  const client = useQueryClient();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [editedId, setEditedId] = useState<string>();
  const [chosen, setChosen] = useState<Set<string>>(new Set(SUGGESTED));
  const [preview, setPreview] = useState<{ change: CatalogChange; sent: string }>();
  const [created, setCreated] = useState<{ change: CatalogChange; id: string; title: string }>();

  const id = editedId ?? applicationId(name);
  const problem = applicationIdProblem(id, applications.map((a) => a.name));
  const body: SaveApplication = {
    title: name.trim(),
    owner: owner.trim(),
    environments: ENVIRONMENTS.filter((e) => chosen.has(e.name)).map(({ name: env, title, production }) => ({ name: env, title, production })),
  };
  const sent = JSON.stringify([id, body]);
  const stale = preview !== undefined && preview.sent !== sent;

  const save = useMutation({
    mutationFn: (base: string | undefined) => saveApplication(id, base === undefined ? body : { ...body, base }),
    onSuccess: (change) => {
      if (!change.written) {
        setPreview({ change, sent });
        return;
      }
      setCreated({ change, id, title: body.title });
      setPreview(undefined);
      selectApplication(id);
      setStep(2);
      void client.invalidateQueries({ queryKey: ["applications"] });
      void client.invalidateQueries({ queryKey: ["setup"] });
    },
  });

  const toggle = (env: string, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(env);
    else next.delete(env);
    setChosen(next);
  };

  let content;
  if (step === 0) {
    content = (
      <section className="card wizard-form" aria-labelledby="application-step">
        <h2 id="application-step">What do you want to check?</h2>
        <p className="hint">An application is a product area: everything one team looks after, across all its environments.</p>
        <label>
          Name
          <input value={name} placeholder="such as Card access" onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Owning team
          <input value={owner} placeholder="such as access-team" onChange={(e) => setOwner(e.target.value)} />
        </label>
        {editedId === undefined ? (
          <p className="hint">
            Id <code>{id || "…"}</code>: it names the application's folder and appears in every URL.{" "}
            <button className="btn-link" type="button" onClick={() => setEditedId(id)}>
              Change the id
            </button>
          </p>
        ) : (
          <label>
            Id
            <input
              className="mono"
              value={editedId}
              aria-invalid={problem ? true : undefined}
              aria-describedby="application-id-problem"
              onChange={(e) => setEditedId(e.target.value.trim())}
            />
          </label>
        )}
        {name.trim() !== "" && problem && (
          <p className="form-error" id="application-id-problem" role="alert">
            {problem}
          </p>
        )}
      </section>
    );
  } else if (step === 1) {
    const where = setup.data ? filePath(setup.data.config_folder, id) : undefined;
    content = (
      <section className="card wizard-form setup-environments" aria-labelledby="environments-step">
        <h2 id="environments-step">Environments</h2>
        <p className="hint">The copies of {body.title || "it"} that probes can read. Each gets its own connection in the next step.</p>
        <fieldset className="env-picker">
          <legend className="sr-only">Environments of {body.title || id}</legend>
          {ENVIRONMENTS.map((env) => (
            <label key={env.name} className="env-choice" data-production={env.production || undefined}>
              <input type="checkbox" checked={chosen.has(env.name)} onChange={(e) => toggle(env.name, e.target.checked)} />
              <span>
                <strong>{env.title}</strong>
                <span className="hint block">{env.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {chosen.size === 0 && (
          <p className="form-error" role="alert">
            Choose at least one environment.
          </p>
        )}
        {where && (
          <p className="hint">
            Nothing is written until Create. Then: <code>{where}</code>
          </p>
        )}
        <div className="wizard-nav">
          <button className="btn secondary" type="button" disabled={chosen.size === 0 || save.isPending} onClick={() => save.mutate(undefined)}>
            Preview
          </button>
          <button className="btn" type="button" disabled={!preview || stale || save.isPending} onClick={() => preview && save.mutate(preview.change.base)}>
            Create {body.title || id}
          </button>
        </div>
        {save.isPending && <p role="status">Working…</p>}
        {preview && <ChangePreview change={preview.change} stale={stale} />}
        {save.error && (
          <div role="alert" className="error-state">
            <p>{save.error.message}</p>
          </div>
        )}
      </section>
    );
  } else if (created) {
    content = (
      <section className="card wizard-form" aria-labelledby="connection-step">
        <h2 id="connection-step">First connection</h2>
        <p role="status" className="written">
          {created.title} was created: <code>{created.change.path}</code>. Review and commit it with git.
        </p>
        <p className="hint">Next, connect it to a database or API in one of its environments. Passwords go to this machine's keychain, never to a file.</p>
        <div className="wizard-nav">
          <RouterLink className="btn" to="/connectors">
            Add its first connection
          </RouterLink>
          <RouterLink className="btn btn-outline" to="/inspect">
            Later
          </RouterLink>
        </div>
      </section>
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Set up ProbeOwl</h1>
          <p className="hint">{applications.length === 0 ? "Your first application" : "A new application"} · owl init does the same from a terminal</p>
        </div>
      </div>
      {setup.isError && (
        <div role="alert" className="error-state">
          <p>{setup.error.message}</p>
          <button className="btn" type="button" onClick={() => void setup.refetch()}>
            Retry
          </button>
        </div>
      )}
      {setup.data?.store.created_now && step < 2 && (
        <p className="hint setup-note" role="note">
          ProbeOwl made its store when it started, so every setup change is recorded in its audit.
        </p>
      )}
      {setup.data && !setup.data.keychain.available && (
        <div role="alert" className="error-state">
          <p>
            This machine has no system keychain, so ProbeOwl can't keep a connection's password here: {setup.data.keychain.reason ?? "no reason given"}.
          </p>
        </div>
      )}
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((label, i) => (
          <li key={label} aria-current={i === step ? "step" : undefined}>
            {label}
          </li>
        ))}
      </ol>
      {content}
      {step === 0 && (
        <div className="wizard-nav">
          <button className="btn" type="button" disabled={problem !== undefined || name.trim() === "" || owner.trim() === ""} onClick={() => setStep(1)}>
            Next: Environments
          </button>
          <GuideLink page="first-application/">Your first application</GuideLink>
        </div>
      )}
      {step === 1 && (
        <div className="wizard-nav">
          <button className="btn btn-outline" type="button" onClick={() => setStep(0)}>
            Back
          </button>
        </div>
      )}
    </>
  );
}
