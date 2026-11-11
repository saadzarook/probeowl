// Author: @SaadZarook
import { useState } from "react";
import type { ConnectorStatus, KillTarget } from "../api/client";
import { useTestConnection } from "../api/useConnectors";
import { useKillSwitches } from "../api/useKillSwitches";
import { broaderSwitch, switchOn, targetText } from "../killSwitches";
import { KillSwitchDialog } from "./KillSwitchDialog";
import { HealthChip } from "./HealthChip";
import { canTestConnection, lagText, limitsText, needsAttention, planText, typeTitle } from "../connectors";

/** What one connector's reading says: reason, lag and its limit, plan access and the guards a lookup runs under. */
export function ConnectorDetail({ status, application }: { status: ConnectorStatus; application: string }) {
  const guards = status.guards ?? [];
  const forbidden = status.forbidden ?? [];
  const test = useTestConnection(application, status.connector, status.environment);
  const switches = useKillSwitches();
  const [changing, setChanging] = useState(false);
  const target: KillTarget = { scope: "connector", application, connector: status.connector };
  const on = switchOn(switches.data ?? [], target) !== undefined;
  const broader = broaderSwitch(switches.data ?? [], application);
  return (
    <section className="connector-detail" aria-label={`Connector ${status.connector}`}>
      <h2>
        <code>{status.connector}</code> <HealthChip health={status.health} />
      </h2>
      <p className="hint">{[typeTitle(status.type), status.role, status.owner].filter(Boolean).join(" · ")}</p>
      {needsAttention(status) && status.reason && (
        <p className="connector-reason" data-health={status.health}>
          {status.reason}
        </p>
      )}
      <dl>
        {!needsAttention(status) && (
          <>
            <dt>Reason</dt>
            <dd>{status.reason ?? "None, nothing to report."}</dd>
          </>
        )}
        <dt>Limits</dt>
        <dd>
          {limitsText(status)}
          {status.type !== "rest" && status.row_ceiling != null && <span className="hint"> · a many location can set its own row cap, up to {status.row_ceiling.toLocaleString("en")}</span>}
        </dd>
        <dt>Replica lag</dt>
        <dd>{lagText(status)}</dd>
        <dt>Pause when lag exceeds</dt>
        <dd>{status.max_replica_lag_seconds == null ? "n/a" : `${status.max_replica_lag_seconds} s`}</dd>
        <dt>Transport</dt>
        <dd>{status.transport ?? "n/a"}</dd>
        {status.type === "oracle" && (
          <>
            {/* What CURRENT_DATE returns in, and how a TIMESTAMP compares with a time with an offset. */}
            <dt>Session time zone</dt>
            <dd>{status.time_zone ?? "n/a"}</dd>
          </>
        )}
        <dt>Plan access</dt>
        <dd>{planText(status)}</dd>
        <dt>Guards · {typeTitle(status.type)}</dt>
        <dd>
          {guards.length === 0 ? (
            "None, this connector has no binding here."
          ) : (
            <ul className="guards" aria-label="Guards">
              {guards.map((guard) => (
                <li key={guard}>{guard}</li>
              ))}
            </ul>
          )}
        </dd>
        {forbidden.length > 0 && (
          <>
            <dt>Forbidden</dt>
            <dd className="connector-forbidden">{forbidden.join(", ")}</dd>
          </>
        )}
      </dl>
      <div className="kill-switch">
        <label>
          {/* Opens the dialog rather than changing at once: a reason is required either way. */}
          <input
            type="checkbox"
            checked={on}
            disabled={switches.isError}
            onChange={() => setChanging(true)}
          />{" "}
          Kill switch
        </label>
        <p className="hint">Stops every read through this connector, in every environment.</p>
        {broader && <p className="hint">Also stopped by the switch on {targetText(broader)}: turning this one off will not resume it.</p>}
      </div>
      <div className="connector-test">
        <button className="btn btn-outline" type="button" disabled={!canTestConnection(status) || test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
        {test.isError && (
          <p className="connector-test-result" role="alert" data-reachable="false">
            {test.error.message}
          </p>
        )}
        {test.isSuccess && (
          <p className="connector-test-result" role="status" data-reachable={test.data.reachable}>
            {test.data.reachable ? `Reached in ${test.data.duration_ms} ms.` : `Not reached: ${test.data.reason ?? "no reason given."}`}
          </p>
        )}
      </div>
      {changing && <KillSwitchDialog target={target} on={!on} onClose={() => setChanging(false)} />}
    </section>
  );
}
