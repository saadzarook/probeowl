// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
import { DEFAULT_PORTS, parseConnectionString, type ConnectionType } from "../connectionString";
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
  const [pasted, setPasted] = useState<string>();
  const [preview, setPreview] = useState<{ change: CatalogChange; sent: string }>();
  const [saved, setSaved] = useState<{ change: CatalogChange; connector: string; environment: string; sql: boolean }>();
  const [passwordState, setPasswordState] = useState<"saving" | "exists" | "saved" | "kept" | "none">();

  const test = useMutation({ mutationFn: (body: DraftConnection) => testDraftConnection(application?.name ?? "", body) });
  const save = useMutation({
    mutationFn: ({ body, base }: { body: SaveConnector; base?: string }) =>
      saveConnector(application?.name ?? "", connector, base === undefined ? body : { ...body, base }),
  });
  // The binding is passed in, not read from state: the save runs right after setSaved, before a render.
  const secret = useMutation({
    mutationFn: ({ binding, replace }: { binding: { connector: string; environment: string }; replace: boolean }) =>
      saveSecret(application?.name ?? "", binding.connector, binding.environment, password, replace),
    onSuccess: () => setPasswordState("saved"),
    onError: (error) => {
      if (error instanceof SecretExistsError) setPasswordState("exists");
    },
  });
  const tested = useMutation({
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
  const body: SaveConnector | undefined =
    type && env ? { type, owner: null, environment: env.name, binding: bindingOf(type, fields) } : undefined;
  const sent = JSON.stringify([connector, body]);
  const stale = preview !== undefined && preview.sent !== sent;

  function draftBody(): DraftConnection {
    return { connector, type: type ?? "sqlserver", environment: envName, binding: bindingOf(type ?? "sqlserver", fields), password: password || null, use_saved_password: false };
  }

  const fill = (text: string) => {
    const parsed = parseConnectionString(text);
    setPaste("");
    if (!parsed) {
      setPasted("That isn't a connection string ProbeOwl can read: JDBC, ODBC or a URL. Nothing was kept.");
      return;
    }
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
    setPasted(
      `Filled from the connection string${parsed.password !== undefined ? ", its password moved to the password field" : ""}. The string itself isn't kept or sent.`,
    );
  };

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
                <button className="btn" type="button" onClick={() => void secret.mutateAsync({ binding: saved, replace: true }).then(() => tested.mutate())}>
                  Overwrite it
                </button>
                <button className="btn btn-outline" type="button" onClick={() => { setPasswordState("kept"); tested.mutate(); }}>
                  Keep the saved one
                </button>
              </div>
            </div>
          )}
          {secret.isError && !(secret.error instanceof SecretExistsError) && (
            <div role="alert" className="error-state">
              <p>{secret.error.message}</p>
              <button className="btn" type="button" onClick={() => secret.mutate({ binding: saved, replace: false })}>Retry</button>
            </div>
          )}
          {tested.isPending && <p role="status">Testing the connection with the saved password…</p>}
          {tested.data && <TestResult result={tested.data} />}
          {tested.isError && <p role="alert" className="form-error">{tested.error.message}</p>}
          {saved.sql && tested.data?.reachable && env && <ConfirmSuggested application={application} connector={saved.connector} environment={env} />}
          <div className="wizard-nav">
            <RouterLink className="btn btn-outline" to="/connectors">Back to Connectors</RouterLink>
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
          <label>
            Paste a connection string (optional)
            <input
              className="mono"
              value={paste}
              placeholder="jdbc:…, Server=…;Database=…, or mariadb://…"
              autoComplete="off"
              onChange={(e) => setPaste(e.target.value)}
              onPaste={(e) => { e.preventDefault(); fill(e.clipboardData.getData("text")); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fill(paste); } }}
            />
          </label>
          <p className="hint">Read in this browser into the fields below. It's never sent or stored; a password in it moves to the password field.</p>
          {pasted && <p role="status" className="hint">{pasted}</p>}
          <p className="hint">
            For <strong>{env?.title ?? "no environment"}</strong>, the environment in the top bar: choose another there.
          </p>
          <div className="form-grid">
            <label>
              Connector id
              <input
                className="mono"
                value={connector}
                disabled={existing !== undefined}
                aria-invalid={connector !== "" && !ID.test(connector) ? true : undefined}
                onChange={(e) => setConnectorId(e.target.value.trim())}
              />
            </label>
          </div>
          {type === "rest" ? (
            <>
              <label>
                Base URL
                <input className="mono" value={fields.baseUrl} placeholder="https://api.example.com/v1" onChange={(e) => update({ baseUrl: e.target.value })} />
              </label>
              <label className="check">
                <input type="checkbox" checked={fields.needsToken} onChange={(e) => update({ needsToken: e.target.checked })} />
                It needs a token
              </label>
              {fields.needsToken && (
                <label>
                  Token
                  <input type="password" value={password} autoComplete="off" placeholder="saved to this machine's keychain" onChange={(e) => setPassword(e.target.value)} />
                </label>
              )}
            </>
          ) : type ? (
            <>
              <div className="form-grid">
                <label>
                  Host
                  <input className="mono" value={fields.host} placeholder="db.example.internal" onChange={(e) => update({ host: e.target.value })} />
                </label>
                <label>
                  Port
                  <input className="mono" inputMode="numeric" value={fields.port} placeholder={String(DEFAULT_PORTS[type])} onChange={(e) => update({ port: e.target.value.replace(/\D/g, "") })} />
                </label>
                <label>
                  {type === "oracle" ? "Service name" : "Database"}
                  <input className="mono" value={fields.database} onChange={(e) => update({ database: e.target.value })} />
                </label>
                {type === "oracle" && (
                  <label>
                    Schema (optional)
                    <input className="mono" value={fields.schema} onChange={(e) => update({ schema: e.target.value })} />
                  </label>
                )}
                <label>
                  Read-only login
                  <input className="mono" value={fields.user} autoComplete="off" onChange={(e) => update({ user: e.target.value })} />
                </label>
                <label>
                  Password
                  <input type="password" value={password} autoComplete="off" placeholder="saved to this machine's keychain" onChange={(e) => setPassword(e.target.value)} />
                </label>
              </div>
              <label className="check" data-production={env?.production || undefined}>
                <input type="checkbox" checked={fields.replica} onChange={(e) => update({ replica: e.target.checked })} />
                This is a read replica, readable secondary or Active Data Guard standby
              </label>
              {replicaMissing && (
                <p className="form-error" role="alert">
                  {env?.title}: {NOT_A_REPLICA}
                </p>
              )}
              <div className="form-grid">
                <label>
                  TLS
                  <select value={fields.tls} onChange={(e) => update({ tls: e.target.value as Tls })}>
                    <option value="verify">Verify the certificate</option>
                    <option value="trust">Trust this server's certificate</option>
                    {type !== "sqlserver" && <option value="none">No TLS</option>}
                  </select>
                </label>
                {fields.tls !== "verify" && (
                  <label>
                    {fields.tls === "trust" ? "Why trust it" : "Why no TLS"}
                    <input value={fields.reason} placeholder={fields.tls === "trust" ? "such as an internal CA" : "such as a local test server"} onChange={(e) => update({ reason: e.target.value })} />
                  </label>
                )}
              </div>
              {reasonMissing && <p className="hint">Say why: it's written beside the binding, and Production requires it.</p>}
            </>
          ) : (
            <p className="hint">Choose a type, or paste a connection string.</p>
          )}
          <div className="wizard-nav">
            <button className="btn secondary" type="button" disabled={!complete || (needsPassword && password === "") || test.isPending} onClick={() => test.mutate(draftBody())}>
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
            <button className="btn secondary" type="button" disabled={!complete || !body || save.isPending} onClick={() => body && void save.mutateAsync({ body }).then((change) => setPreview({ change, sent }))}>
              Preview
            </button>
            <button className="btn" type="button" disabled={!preview || stale || save.isPending} onClick={() => void write()}>
              Save {connector || "connection"}
            </button>
          </div>
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
