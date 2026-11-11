// Author: @SaadZarook
import { Link } from "react-router-dom";
import { useKillSwitches } from "../api/useKillSwitches";
import { targetText } from "../killSwitches";

/** On every screen while a kill switch is on, or while nobody can tell whether one is. */
export function KillSwitchBanner() {
  const switches = useKillSwitches();
  if (switches.isError) {
    return (
      <div className="kill-banner" role="alert">
        <strong>Kill switch unknown.</strong> {switches.error.message} Runs are refused until it can be.
      </div>
    );
  }
  const on = switches.data ?? [];
  if (on.length === 0) return null;
  return (
    <div className="kill-banner" role="alert" aria-label="Kill switches on">
      <strong>Reads stopped.</strong>
      <ul>
        {on.map((s) => (
          <li key={`${s.scope}/${s.application ?? ""}/${s.connector ?? ""}`}>
            {targetText(s)}: {s.reason} · by {s.actor.name}
          </li>
        ))}
      </ul>
      <Link to="/connectors">Connectors</Link>
    </div>
  );
}
