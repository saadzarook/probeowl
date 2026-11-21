// Author: @SaadZarook
import { Button, buttonClass, Field, FormGrid, Input, Select } from "../ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import {
  saveConnector,
  saveSecret,
  SecretExistsError,
  testDraftConnection,
  type CatalogChange,
  type ConnectionTest,
  type DraftConnection,
  type SaveConnector,
} from "../api/client";
import { ChangePreview } from "../components/ChangePreview";
import { ConfirmSuggested } from "../components/ConfirmSuggested";
import { EmptyState, GuideLink } from "../components/EmptyState";
import { DEFAULT_PORTS, parseConnectionString, withoutPassword, type ConnectionType } from "../connectionString";
import { useSelection } from "../selection";

const TYPES: { type: ConnectionType; title: string; hint: string }[] = [
  { type: "sqlserver", title: "SQL Server", hint: "2016 and later" },
  { type: "oracle", title: "Oracle", hint: "19c and later" },
  { type: "mariadb", title: "MariaDB", hint: "10.6 and later" },
  { type: "rest", title: "REST API", hint: "GET only" },
];
type Tls = "verify" | "trust" | "none";
const NOT_A_REPLICA =
  "ProbeOwl reads Production only from a read replica, readable secondary or Active Data Guard standby, never from the primary, so a probe can never slow or block it. Ask your DBA for one.";
const ID = /^[a-z][a-z0-9_]*$/;

interface Fields {
  host: string;
  port: string;
  database: string;
  schema: string;
  user: string;
  replica: boolean;
  tls: Tls;
  reason: string;
  baseUrl: string;
  needsToken: boolean;
}
const EMPTY: Fields = { host: "", port: "", database: "", schema: "", user: "", replica: false, tls: "verify", reason: "", baseUrl: "", needsToken: false };

/** The binding as the API takes it; never a password or its reference. */
function bindingOf(type: ConnectionType, f: Fields): SaveConnector["binding"] {
  if (type === "rest") return { base_url: f.baseUrl.trim(), needs_token: f.needsToken };
  const reason = f.reason.trim() || null;
  return {
    host: f.host.trim(),
    port: Number(f.port || DEFAULT_PORTS[type]),
    database: f.database.trim(),
    ...(type === "oracle" && f.schema.trim() ? { schema: f.schema.trim() } : {}),
    user: f.user.trim(),
    replica: f.replica,
    encrypt: f.tls !== "none",
    encrypt_reason: f.tls === "none" ? reason : null,
    trust_server_certificate: f.tls === "trust",
    trust_server_certificate_reason: f.tls === "trust" ? reason : null,
  };
}

/** Add connection: a new connector, or a binding for an environment not bound yet, with its password kept in this machine's keychain. */
export function AddConnectionPage() {
  const { application, environment, selectEnvironment } = useSelection();
  const [params] = useSearchParams();
  const client = useQueryClient();
  const existing = params.get("connector") ?? undefined;
  const [type, setType] = useState<ConnectionType | undefined>(() => {
    const t = params.get("type");
    return TYPES.some((x) => x.type === t) ? (t as ConnectionType) : undefined;
  });
  // The top bar's environment, as on every screen; a link that names one (first run's) chooses it there.
  const asked = params.get("environment");
  useEffect(() => {
    if (asked && application?.environments.some((e) => e.name === asked)) selectEnvironment(asked);
    // Once, for the link that opened the form: later choices are the top bar's.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application?.name]);
  const envName = environment?.name ?? "";
  const [connectorId, setConnectorId] = useState<string>();
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [password, setPassword] = useState("");
  const [paste, setPaste] = useState("");
  // The string as last filled from, password taken out: leaving the field unchanged fills nothing again.
  const [shown, setShown] = useState("");
  const [pasted, setPasted] = useState<string>();
  // The fields the last string filled, highlighted for a few seconds (#92).
  const [justFilled, setJustFilled] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (justFilled.length === 0) return;
    const timer = window.setTimeout(() => setJustFilled([]), 4000);
    return () => window.clearTimeout(timer);
  }, [justFilled]);
  useEffect(() => {
    const open = dialog.current;
    if (confirming && open && !open.open) open.showModal();
    if (!confirming && open?.open) open.close();
  }, [confirming]);
  const [preview, setPreview] = useState<{ change: CatalogChange; sent: string }>();
  const [saved, setSaved] = useState<{ change: CatalogChange; connector: string; environment: string; sql: boolean }>();
  const [passwordState, setPasswordState] = useState<"saving" | "exists" | "saved" | "kept" | "none">();

  // No password in a mutation's variables, and each mutation dropped from the cache as the form
  // goes (gcTime 0): React Query would otherwise keep a mutation, and what it was called with,
  // for five minutes after the form is gone.
  const test = useMutation({ gcTime: 0, mutationFn: () => testDraftConnection(application?.name ?? "", draftBody()) });
  const save = useMutation({
    gcTime: 0,
    mutationFn: ({ body, base }: { body: SaveConnector; base?: string }) =>
      saveConnector(application?.name ?? "", connector, base === undefined ? body : { ...body, base }),
  });
  // The binding is passed in, not read from state: the save runs right after setSaved, before a render.
  const secret = useMutation({
    gcTime: 0,
    mutationFn: ({ binding, replace }: { binding: { connector: string; environment: string }; replace: boolean }) =>
      saveSecret(application?.name ?? "", binding.connector, binding.environment, password, replace),
    onSuccess: () => setPasswordState("saved"),
    onError: (error) => {
      if (error instanceof SecretExistsError) setPasswordState("exists");
    },
  });
  const tested = useMutation({
    gcTime: 0,
    mutationFn: () => testDraftConnection(application?.name ?? "", { ...draftBody(), password: null, use_saved_password: true }),
  });

  if (!application) {
    return <EmptyState level={1} title="Choose an application" message="A connection belongs to one application: choose it in the top bar." />;
  }
  const env = application.environments.find((e) => e.name === envName);
  const connector = existing ?? connectorId ?? (type ? `${application.name}_${type === "rest" ? "api" : type}` : "");
  const update = (patch: Partial<Fields>) => setFields((f) => ({ ...f, ...patch }));
  const sql = type !== undefined && type !== "rest";
  const needsPassword = sql || (type === "rest" && fields.needsToken);
  const replicaMissing = sql && env?.production === true && !fields.replica;
  const reasonMissing = sql && fields.tls !== "verify" && fields.reason.trim() === "";
  const complete =
    type !== undefined &&
    env !== undefined &&
    ID.test(connector) &&
    (type === "rest"
      ? fields.baseUrl.trim() !== ""
      : fields.host.trim() !== "" && fields.database.trim() !== "" && fields.user.trim() !== "" && !replicaMissing && !reasonMissing);
  // What Save still needs, said beside it rather than a silently disabled button (#98).
  const missing = [
    type === undefined && "a type",
    !ID.test(connector) && "a connector id",
    type === "rest" && fields.baseUrl.trim() === "" && "the base URL",
    sql && fields.host.trim() === "" && "the host",
    sql && fields.database.trim() === "" && (type === "oracle" ? "the service name" : "the database"),
    sql && fields.user.trim() === "" && "the login",
    replicaMissing && "the read replica tick",
    reasonMissing && "why TLS is not verified",
  ].filter((m): m is string => typeof m === "string");
  const body: SaveConnector | undefined =
    type && env ? { type, owner: null, environment: env.name, binding: bindingOf(type, fields) } : undefined;
  const sent = JSON.stringify([connector, body]);
  const stale = preview !== undefined && preview.sent !== sent;

  function draftBody(): DraftConnection {
    return { connector, type: type ?? "sqlserver", environment: envName, binding: bindingOf(type ?? "sqlserver", fields), password: password || null, use_saved_password: false };
  }

  const fill = (text: string) => {
    const parsed = parseConnectionString(text);
    if (!parsed) {
      setPaste("");
      setShown("");
      setJustFilled([]);
      setPasted("That isn't a connection string ProbeOwl can read: JDBC, ODBC or a URL. Nothing was kept.");
      return;
    }
    // What stays on screen: the string without its password, which goes to the password field
    // alone. Should the password still be in it, nothing stays.
    const kept = withoutPassword(text);
    const safe = parsed.password !== undefined && kept.includes(parsed.password) ? "" : kept;
    setPaste(safe);
    setShown(safe);
    if (parsed.type && !existing) setType(parsed.type);
    update({
      ...(parsed.host !== undefined ? { host: parsed.host } : {}),
      ...(parsed.port !== undefined ? { port: String(parsed.port) } : {}),
      ...(parsed.database !== undefined ? { database: parsed.database } : {}),
      ...(parsed.user !== undefined ? { user: parsed.user } : {}),
      ...(parsed.baseUrl !== undefined ? { baseUrl: parsed.baseUrl } : {}),
      ...(parsed.encrypt === false ? { tls: "none" as const } : parsed.trustServerCertificate ? { tls: "trust" as const } : {}),
    });
    if (parsed.password !== undefined) setPassword(parsed.password);
    const named: [keyof typeof parsed, string][] = [["host", "host"], ["port", "port"], ["database", "database"], ["user", "login"], ["baseUrl", "base URL"]];
    const filled = named.filter(([key]) => parsed[key] !== undefined);
    setJustFilled(filled.map(([key]) => key));
    const count = `${filled.length} ${filled.length === 1 ? "field" : "fields"}`;
    const which = filled.length > 0 ? ` (${filled.map(([, title]) => title).join(", ")})` : "";
    setPasted(
      `Filled ${count}${which} from the connection string${parsed.password !== undefined ? "; the password moved to the password field" : ""}. The string isn't sent or stored.`,
    );
  };
  const clearPaste = () => {
    setPaste("");
    setShown("");
    setPasted(undefined);
    setJustFilled([]);
  };
  const lit = (key: string) => (justFilled.includes(key) ? true : undefined);

  const write = async () => {
    if (!body || !preview) return;
    const change = await save.mutateAsync({ body, base: preview.change.base });
    setSaved({ change, connector, environment: envName, sql });
    setPreview(undefined);
    void client.invalidateQueries({ queryKey: ["connectors"] });
    void client.invalidateQueries({ queryKey: ["applications"] });
    if (needsPassword && password !== "") {
      setPasswordState("saving");
      try {
        await secret.mutateAsync({ binding: { connector, environment: envName }, replace: false });
      } catch {
        return; // shown below: the question before overwriting, or the error
      }
    } else setPasswordState("none");
    tested.mutate();
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Add connection</h1>
          <p className="hint">
            {application.title} · read only · passwords go to this machine's keychain, never to a file
          </p>
        </div>
      </div>
      {saved ? (
        <section className="card wizard-form" aria-labelledby="saved-step">
          <h2 id="saved-step">{saved.connector} is connected in {env?.title ?? saved.environment}</h2>
          <p role="status" className="written">
            Written to <code>{saved.change.path}</code>. Review and commit it with git.
          </p>
          {passwordState === "saving" && <p role="status">Saving the password to this machine's keychain…</p>}
          {passwordState === "saved" && <p role="status">The password is in this machine's keychain. No file or log has it.</p>}
          {passwordState === "kept" && <p role="status">The password already saved for this connection was kept.</p>}
          {passwordState === "exists" && (
            <div role="alertdialog" aria-labelledby="overwrite-title" className="overwrite">
              <p id="overwrite-title">
                <strong>A password is already saved for {saved.connector} in {env?.title ?? saved.environment}</strong>, perhaps by another application's connector of the same id. Overwrite it?
              </p>
              <div className="wizard-nav">
                <Button onClick={() => void secret.mutateAsync({ binding: saved, replace: true }).then(() => tested.mutate())}>
                  Overwrite it
                </Button>
                <Button variant="outline" onClick={() => { setPasswordState("kept"); tested.mutate(); }}>
                  Keep the saved one
                </Button>
              </div>
            </div>
          )}
          {secret.isError && !(secret.error instanceof SecretExistsError) && (
            <div role="alert" className="error-state">
              <p>{secret.error.message}</p>
              <Button onClick={() => secret.mutate({ binding: saved, replace: false })}>Retry</Button>
            </div>
          )}
          {tested.isPending && <p role="status">Testing the connection with the saved password…</p>}
          {tested.data && <TestResult result={tested.data} />}
          {tested.isError && <p role="alert" className="form-error">{tested.error.message}</p>}
          {saved.sql && tested.data?.reachable && env && <ConfirmSuggested application={application} connector={saved.connector} environment={env} />}
          <div className="wizard-nav">
            <RouterLink className={buttonClass({ variant: "outline" })} to="/connectors">Back to Connectors</RouterLink>
          </div>
        </section>
      ) : (
        <section className="card wizard-form add-connection" aria-labelledby="connection-step">
          <h2 id="connection-step">{existing ? `A binding for ${existing}` : "A new connection"}</h2>
          <div role="group" aria-label="Type" className="type-tiles">
            {TYPES.map((t) => (
              <button
                key={t.type}
                type="button"
                className="type-tile"
                aria-pressed={type === t.type}
                disabled={existing !== undefined && type !== t.type}
                onClick={() => { setType(t.type); setPreview(undefined); }}
              >
                <strong>{t.title}</strong>
                <span className="hint block">{t.hint}</span>
              </button>
            ))}
          </div>
          <Field
            id="connection-paste"
            label="Paste a connection string (optional)"
            help="Paste or type a JDBC, ODBC or URL connection string, then press Enter. It's read in this browser into the fields below, and never sent or stored; a password in it moves to the password field."
          >
            <Input
              className="font-mono"
              value={paste}
              placeholder="jdbc:…, Server=…;Database=…, or mariadb://…"
              autoComplete="off"
              onChange={(e) => setPaste(e.target.value)}
              onPaste={(e) => { e.preventDefault(); fill(e.clipboardData.getData("text")); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fill(paste); } }}
              // Typed, not pasted: leaving the field fills the form too (#97).
              onBlur={() => { if (paste.trim() !== "" && paste !== shown) fill(paste); }}
            />
          </Field>
          {(pasted || paste !== "") && (
            <p role="status" className="hint">
              {pasted}{" "}
              <button className="btn-link" type="button" onClick={clearPaste}>
                Clear
              </button>
            </p>
          )}
          <p className="hint">
            For <strong>{env?.title ?? "no environment"}</strong>, the environment in the top bar: choose another there.
          </p>
          <FormGrid className="xl:grid-cols-3">
            <Field id="connection-id" label="Connector id">
              <Input
                className="font-mono"
                value={connector}
                disabled={existing !== undefined}
                aria-invalid={connector !== "" && !ID.test(connector) ? true : undefined}
                onChange={(e) => setConnectorId(e.target.value.trim())}
              />
            </Field>
          </FormGrid>
          {type === "rest" ? (
            <>
              <FormGrid className="xl:grid-cols-3">
                <Field id="connection-base-url" label="Base URL">
                  <Input className="font-mono" data-filled={lit("baseUrl")} value={fields.baseUrl} placeholder="https://api.example.com/v1" onChange={(e) => update({ baseUrl: e.target.value })} />
                </Field>
              </FormGrid>
              <label className="check">
                <input type="checkbox" checked={fields.needsToken} onChange={(e) => update({ needsToken: e.target.checked })} />
                It needs a token
              </label>
              {fields.needsToken && (
                <FormGrid className="xl:grid-cols-3">
                  <Field id="connection-token" label="Token">
                    <Input type="password" value={password} autoComplete="new-password" placeholder="saved to this machine's keychain" onChange={(e) => setPassword(e.target.value)} />
                  </Field>
                </FormGrid>
              )}
            </>
          ) : type ? (
            <>
              <FormGrid className="xl:grid-cols-3">
                <Field id="connection-host" label="Host">
                  <Input className="font-mono" data-filled={lit("host")} value={fields.host} placeholder="db.example.internal" onChange={(e) => update({ host: e.target.value })} />
                </Field>
                <Field id="connection-port" label="Port">
                  <Input className="font-mono" data-filled={lit("port")} inputMode="numeric" value={fields.port} placeholder={String(DEFAULT_PORTS[type])} onChange={(e) => update({ port: e.target.value.replace(/\D/g, "") })} />
                </Field>
                <Field id="connection-database" label={type === "oracle" ? "Service name" : "Database"}>
                  <Input className="font-mono" data-filled={lit("database")} value={fields.database} onChange={(e) => update({ database: e.target.value })} />
                </Field>
                {type === "oracle" && (
                  <Field id="connection-schema" label="Schema (optional)">
                    <Input className="font-mono" value={fields.schema} onChange={(e) => update({ schema: e.target.value })} />
                  </Field>
                )}
                <Field id="connection-user" label="Read-only login">
                  <Input className="font-mono" data-filled={lit("user")} value={fields.user} autoComplete="off" onChange={(e) => update({ user: e.target.value })} />
                </Field>
                <Field id="connection-password" label="Password">
                  <Input type="password" value={password} autoComplete="new-password" placeholder="saved to this machine's keychain" onChange={(e) => setPassword(e.target.value)} />
                </Field>
              </FormGrid>
              <label className="check" data-production={env?.production || undefined}>
                <input type="checkbox" checked={fields.replica} onChange={(e) => update({ replica: e.target.checked })} />
                This is a read replica, readable secondary or Active Data Guard standby
              </label>
              {replicaMissing && (
                <p className="form-error" role="alert">
                  {env?.title}: {NOT_A_REPLICA}
                </p>
              )}
              <FormGrid className="xl:grid-cols-3">
                <Field id="connection-tls" label="TLS">
                  <Select value={fields.tls} onChange={(e) => update({ tls: e.target.value as Tls })}>
                    <option value="verify">Verify the certificate</option>
                    <option value="trust">Trust this server's certificate</option>
                    {type !== "sqlserver" && <option value="none">No TLS</option>}
                  </Select>
                </Field>
                {fields.tls !== "verify" && (
                  <Field
                    id="connection-tls-reason"
                    label={fields.tls === "trust" ? "Why trust it" : "Why no TLS"}
                    help={reasonMissing ? "Say why: it's written beside the binding, and Production requires it." : undefined}
                  >
                    <Input value={fields.reason} placeholder={fields.tls === "trust" ? "such as an internal CA" : "such as a local test server"} onChange={(e) => update({ reason: e.target.value })} />
                  </Field>
                )}
              </FormGrid>
            </>
          ) : (
            <p className="hint">Choose a type, or paste a connection string.</p>
          )}
          <div className="flex flex-wrap items-start gap-2">
            <Button variant="outline" disabled={!complete || (needsPassword && password === "") || test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Testing…" : "Test connection"}
            </Button>
            <Button variant="outline" disabled={!complete || !body || save.isPending} onClick={() => body && void save.mutateAsync({ body }).then((change) => setPreview({ change, sent }))}>
              Preview
            </Button>
            <Button
              disabled={!complete || !body || save.isPending}
              disabledReason={missing.length > 0 ? `To save: ${missing.join(", ")}.` : undefined}
              onClick={() => {
                // As the probe editor saves: the preview in a dialog, then exactly that is written.
                setConfirming(true);
                if (body && (!preview || stale)) void save.mutateAsync({ body }).then((change) => setPreview({ change, sent }));
              }}
            >
              Save {connector || "connection"}
            </Button>
          </div>
          <dialog ref={dialog} className="import-dialog save-dialog" aria-labelledby="save-connection-title" onClose={() => setConfirming(false)}>
            <h2 id="save-connection-title">Save {connector}</h2>
            {confirming && preview && !stale ? (
              <ChangePreview change={preview.change} stale={false} />
            ) : save.isError ? null : (
              <p aria-busy="true">Preparing the preview…</p>
            )}
            {confirming && save.isError && <p role="alert" className="form-error">{save.error.message}</p>}
            <div className="dialog-actions">
              <Button
                disabled={!preview || stale || save.isPending}
                disabledReason={save.isPending ? undefined : !preview || stale ? "Waiting for the preview" : undefined}
                onClick={() => {
                  setConfirming(false);
                  void write();
                }}
              >
                Write {connector}
              </Button>
              <Button variant="outline" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </dialog>
          {test.data && <TestResult result={test.data} />}
          {test.isError && <p role="alert" className="form-error">{test.error.message}</p>}
          {preview && <ChangePreview change={preview.change} stale={stale} />}
          {save.isError && <div role="alert" className="error-state"><p>{save.error.message}</p></div>}
          <GuideLink page="connectors/#adding-a-connector">Adding a connector</GuideLink>
        </section>
      )}
    </>
  );
}

function TestResult({ result }: { result: ConnectionTest }) {
  return result.reachable ? (
    <p role="status" className="result-ok">
      <strong>Connected</strong> in {result.duration_ms} ms.
    </p>
  ) : (
    <p role="alert" className="result-bad">
      <strong>Not connected:</strong> {result.reason}
    </p>
  );
}
