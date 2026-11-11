// Author: @SaadZarook
import { useLocation } from "react-router-dom";
import { initials, useMe } from "../api/useMe";
import { isToolPath } from "../plugins";
import { useSelection } from "../selection";
import { ApplicationSelector } from "./ApplicationSelector";

/** Top bar: application selector, environment and the current user. */
export function TopBar() {
  const { application, environment, selectEnvironment } = useSelection();
  const me = useMe();
  const choices = application?.environments ?? [];
  // A tool uses no application or environment, so a red Production selector would mislead.
  const tool = isToolPath(useLocation().pathname);

  return (
    <header className="topbar">
      <span id="app-label" className="field-label">Application</span>
      <ApplicationSelector />
      {tool && <span className="hint">not used by this tool</span>}
      {!tool && (
        <>
          <label htmlFor="environment">Environment</label>
          <select
            id="environment"
            className="env-select"
            data-production={environment?.production === true}
            disabled={!application}
            value={environment?.name ?? ""}
            onChange={(e) => {
              const next = choices.find((choice) => choice.name === e.target.value);
              if (next) selectEnvironment(next.name);
            }}
          >
            {!application && <option value="">All environments</option>}
            {choices.map((choice) => (
              <option key={choice.name} value={choice.name}>
                {choice.title}
              </option>
            ))}
          </select>
        </>
      )}
      <span className="topbar-spacer" />
      {/* Only the real user, as runs record them; nothing while it loads or if it cannot be read. */}
      {me.data && (
        <div className="user" aria-label={`Signed in as ${me.data.name}`}>
          <span className="avatar" aria-hidden="true">
            {initials(me.data.name)}
          </span>
          <span>{me.data.name}</span>
        </div>
      )}
    </header>
  );
}
