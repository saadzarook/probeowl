// Author: @SaadZarook
import { useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import type { KillTarget } from "../api/client";
import { useConnectors } from "../api/useConnectors";
import { useKillSwitches } from "../api/useKillSwitches";
import { useReloadConfiguration } from "../api/useReloadConfiguration";
import { ConnectorDetail } from "../components/ConnectorDetail";
import { CopyButton } from "../components/CopyButton";
import { Button, buttonClass, ListOrTable } from "../ui";
import { EmptyState, GuideLink } from "../components/EmptyState";
import { HealthChip } from "../components/HealthChip";
import { KillSwitchDialog } from "../components/KillSwitchDialog";
import { lagText, limitsText, needsAttention, planWarning, typeTitle } from "../connectors";
import { switchOn } from "../killSwitches";
import { reloadSummary } from "../reload";
import type { NavItem } from "../routes";
import { useSelection } from "../selection";

const GLOBAL: KillTarget = { scope: "global" };

/** Connector status of the chosen application in the top bar's environment. */
export function ConnectorsPage({ item }: { item: NavItem }) {
  const { application, environment, selectEnvironment } = useSelection();
  const connectors = useConnectors(application?.name, environment?.name);
  // ?connector= from search opens that one.
  const [params] = useSearchParams();
  const [pick, setPick] = useState<string | undefined>(() => params.get("connector") ?? undefined);
  const switches = useKillSwitches();
  const [changing, setChanging] = useState<{ target: KillTarget; on: boolean }>();
  const reload = useReloadConfiguration();
  const applicationTarget: KillTarget = { scope: "application", application: application?.name ?? null };
  const applicationOn = switchOn(switches.data ?? [], applicationTarget) !== undefined;
  const globalOn = switchOn(switches.data ?? [], GLOBAL) !== undefined;
  const rows = connectors.data ?? [];
  const selected = rows.find((r) => r.connector === pick) ?? rows[0];

  let body;
  if (application === undefined || environment === undefined) {
    body = <EmptyState title="Choose an application" message="Connectors belong to one application." />;
  } else if (connectors.isPending) {
    body = <p aria-busy="true">Loading the connectors…</p>;
  } else if (connectors.isError) {
    body = (
      <div className="error-state" role="alert">
        <p>{connectors.error.message}</p>
        <Button onClick={() => void connectors.refetch()}>Retry</Button>
      </div>
    );
  } else if (rows.length === 0) {
    const command = `owl connector add --application ${application.name}`;
    body = (
      <EmptyState
        title={`No connectors in ${application.title}`}
        message={
          <>
            A connector is a read-only client for one database or API. Run this in the folder owl serve was started with: it asks for one
            binding per environment and keeps passwords in your keychain. Or add a file under <code>{application.name}/connectors/</code>{" "}
            yourself. Then load it: Reload configuration, above, or owl reload.
          </>
        }
      >
        <RouterLink className={buttonClass()} to="/connectors/new">
          Add connection
        </RouterLink>
        <p className="cli-command">
          Or from a terminal: <code>{command}</code> <CopyButton text={command} label="Copy command" />
        </p>
        <GuideLink page="connectors/#adding-a-connector">Adding a connector</GuideLink>
      </EmptyState>
    );
  } else {
    body = (
      <div className="tables-layout connectors-layout">
        <ListOrTable
          label="Connectors"
          rows={rows}
          rowKey={(row) => row.connector}
          highlight={(row) => (needsAttention(row) ? "waiting" : selected?.connector === row.connector ? "selected" : undefined)}
          empty="No connectors yet."
          columns={[
            {
              header: "Connector",
              cell: (row) => {
                const warning = planWarning(row);
                return (
                  <>
                    <button type="button" className="grid cursor-pointer gap-1 border-0 bg-transparent p-0 text-left text-foreground" aria-pressed={selected?.connector === row.connector} onClick={() => setPick(row.connector)}>
                      <code className="font-mono">{row.connector}</code>
                    </button>
                    {row.role && <p className="m-0 mt-1 text-xs text-muted">{row.role}</p>}
                    {warning && (
                      <p className="m-0 mt-2 border-0 border-l-4 border-solid border-waiting-fg bg-waiting-bg px-3 py-1 text-sm text-waiting-fg" role="note">
                        {warning}
                      </p>
                    )}
                  </>
                );
              },
            },
            { header: "Type", cell: (row) => typeTitle(row.type) },
            { header: "Limits", cell: (row) => limitsText(row) },
            { header: "Lag", cell: (row) => <span className="font-mono">{lagText(row)}</span> },
            { header: "Status", cell: (row) => <HealthChip health={row.health} /> },
          ]}
        />
        {selected && <ConnectorDetail key={`${selected.connector}/${selected.environment}`} status={selected} application={application.name} />}
      </div>
    );
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>{item.label}</h1>
          <p className="hint">Bound per environment · production always uses read replicas</p>
        </div>
        <div className="editor-actions">
          {application && (
            <RouterLink className={buttonClass()} to="/connectors/new">
              Add connection
            </RouterLink>
          )}
          <Button variant="outline" disabled={reload.isPending} onClick={() => reload.mutate()}>
            {reload.isPending ? "Reloading…" : "Reload configuration"}
          </Button>
          {application && (
            <>
              <Button variant="outline" disabled={switches.isError} onClick={() => setChanging({ target: applicationTarget, on: !applicationOn })}>
                {applicationOn ? `Resume ${application.title}` : `Stop ${application.title}`}
              </Button>
              <Button variant="outline" disabled={switches.isError} onClick={() => setChanging({ target: GLOBAL, on: !globalOn })}>
                {globalOn ? "Resume everything" : "Stop everything"}
              </Button>
            </>
          )}
        </div>
      </div>
      {reload.isSuccess && (
        <div className="reload-result" role="status">
          {reloadSummary(reload.data).map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      )}
      {reload.isError && (
        <div className="error-state" role="alert">
          <p>{reload.error.message}</p>
          <Button onClick={() => reload.mutate()}>Retry</Button>
        </div>
      )}
      {changing && <KillSwitchDialog target={changing.target} on={changing.on} onClose={() => setChanging(undefined)} />}
      {application && environment && (
        <div className="environment-bar">
          <div role="group" aria-label="Show bindings for" className="environment-tabs">
            {application.environments.map((e) => (
              <button
                key={e.name}
                type="button"
                aria-pressed={e.name === environment.name}
                data-production={e.production}
                onClick={() => selectEnvironment(e.name)}
              >
                {e.title}
              </button>
            ))}
          </div>
          <p className="hint">
            Showing {environment.title} bindings{environment.production ? " · guards locked" : ""}
          </p>
        </div>
      )}
      {body}
    </>
  );
}
