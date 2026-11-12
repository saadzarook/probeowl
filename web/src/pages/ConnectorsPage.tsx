// Author: @SaadZarook
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import type { KillTarget } from "../api/client";
import { useConnectors } from "../api/useConnectors";
import { useKillSwitches } from "../api/useKillSwitches";
import { useReloadConfiguration } from "../api/useReloadConfiguration";
import { ConnectorDetail } from "../components/ConnectorDetail";
import { CopyButton } from "../components/CopyButton";
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
  const [pick, setPick] = useState<string>();
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
        <button className="btn" type="button" onClick={() => void connectors.refetch()}>Retry</button>
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
        <RouterLink className="btn" to="/connectors/new">
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
        <div className="table-scroll">
          <table className="connector-list">
            <thead>
              <tr>
                <th scope="col">Connector</th>
                <th scope="col">Type</th>
                <th scope="col">Limits</th>
                <th scope="col">Lag</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isSelected = selected?.connector === row.connector;
                const warning = planWarning(row);
                return (
                  <tr key={row.connector} data-selected={isSelected} data-attention={needsAttention(row)}>
                    <td>
                      <button type="button" className="table-pick" aria-pressed={isSelected} onClick={() => setPick(row.connector)}>
                        <code>{row.connector}</code>
                      </button>
                      {row.role && <p className="connector-role">{row.role}</p>}
                      {warning && <p className="connector-warning" role="note">{warning}</p>}
                    </td>
                    <td>{typeTitle(row.type)}</td>
                    <td className="connector-limits">{limitsText(row)}</td>
                    <td className="connector-lag">{lagText(row)}</td>
                    <td><HealthChip health={row.health} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
            <RouterLink className="btn" to="/connectors/new">
              Add connection
            </RouterLink>
          )}
          <button className="btn btn-outline" type="button" disabled={reload.isPending} onClick={() => reload.mutate()}>
            {reload.isPending ? "Reloading…" : "Reload configuration"}
          </button>
          {application && (
            <>
              <button className="btn btn-outline" type="button" disabled={switches.isError} onClick={() => setChanging({ target: applicationTarget, on: !applicationOn })}>
                {applicationOn ? `Resume ${application.title}` : `Stop ${application.title}`}
              </button>
              <button className="btn btn-outline" type="button" disabled={switches.isError} onClick={() => setChanging({ target: GLOBAL, on: !globalOn })}>
                {globalOn ? "Resume everything" : "Stop everything"}
              </button>
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
          <button className="btn" type="button" onClick={() => reload.mutate()}>Retry</button>
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
