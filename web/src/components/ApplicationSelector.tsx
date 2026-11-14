// Author: @SaadZarook
import * as Popover from "@radix-ui/react-popover";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import type { ApplicationSummary } from "../api/client";
import { useSelection } from "../selection";

function matches(app: ApplicationSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  return q === "" || app.title.toLowerCase().includes(q) || app.owner.toLowerCase().includes(q);
}

function describe(app: ApplicationSummary): string {
  return `${app.owner} · ${app.environments.map((e) => e.title).join(", ")}`;
}

/** Searchable application selector: recent applications, then all, searchable by name or team. */
export function ApplicationSelector() {
  const { applications, application, selectApplication } = useSelection();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const found = applications.filter((a) => matches(a, query));
  const showAll = query.trim() === "" || "all applications".includes(query.trim().toLowerCase());

  const choose = (name: string | undefined) => {
    selectApplication(name);
    setOpen(false);
    setQuery("");
  };

  const option = (app: ApplicationSummary) => (
    <li key={app.name}>
      <button
        type="button"
        className="app-option"
        aria-current={application?.name === app.name}
        onClick={() => choose(app.name)}
      >
        <span className="name">{app.title}</span>
        <span className="meta">{describe(app)}</span>
      </button>
    </li>
  );

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger id="app-trigger" className="app-trigger" aria-labelledby="app-label app-trigger">
        <span className="grow">{application?.title ?? "All applications"}</span>
        {application && <span className="app-team">{application.owner}</span>}
        <span aria-hidden="true">▾</span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="app-popover" aria-label="Choose application" align="start" sideOffset={6}>
          <input
            className="app-search"
            type="search"
            aria-label="Search applications by name or team"
            placeholder="Search by name or team"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <h3 className="app-group-title">All · {applications.length}</h3>
          <ul className="app-list">
            {showAll && (
              <li>
                <button type="button" className="app-option" aria-current={!application} onClick={() => choose(undefined)}>
                  <span className="name">All applications</span>
                  <span className="meta">Overview screens only: Health, Connectors, Runs</span>
                </button>
              </li>
            )}
            {found.map(option)}
          </ul>
          {found.length === 0 && !showAll && <p className="app-empty">No application matches “{query}”.</p>}
          {/* A fixed path: setup asks for a name not loaded yet. */}
          <RouterLink className="app-new" to="/setup" onClick={() => setOpen(false)}>
            + New application
          </RouterLink>
          <p className="app-note">Your choice carries across every screen</p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
