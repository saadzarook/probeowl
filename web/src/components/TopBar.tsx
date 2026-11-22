// Author: @SaadZarook
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { initials, useMe } from "../api/useMe";
import { isToolPath } from "../plugins";
import { useSelection } from "../selection";
import { Button, Select, Sheet } from "../ui";
import { ApplicationSelector } from "./ApplicationSelector";
import { Brand, Navigation } from "./Sidebar";

// Shown from 1280 px; below that the controls keep their names for screen readers (rule 5).
const LABEL = "sr-only shrink-0 text-sm font-medium text-muted xl:not-sr-only";

/** Top bar in at most two rows: the menu below 1024 px, application and environment, search, and the user. */
export function TopBar({ onSearch }: { onSearch: () => void }) {
  const { application, environment, selectEnvironment } = useSelection();
  const me = useMe();
  const choices = application?.environments ?? [];
  // A tool uses no application or environment, so a red Production selector would mislead.
  const tool = isToolPath(useLocation().pathname);
  const [menu, setMenu] = useState(false);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border py-3">
      <Sheet
        trigger={
          <Button variant="outline" className="lg:hidden">
            Menu
          </Button>
        }
        open={menu}
        onOpenChange={setMenu}
        title={<Brand onNavigate={() => setMenu(false)} />}
        description="The screens of ProbeOwl"
      >
        <Navigation onNavigate={() => setMenu(false)} />
      </Sheet>
      {/* Below 640 px the selects take the second row, so the user never wraps alone. */}
      <div className="order-last flex w-full min-w-0 items-center gap-2 sm:order-none sm:w-auto sm:flex-1">
        <span id="app-label" className={LABEL}>
          Application
        </span>
        <ApplicationSelector />
        {tool && <span className="text-sm text-muted">not used by this tool</span>}
        {!tool && (
          <>
            <label htmlFor="environment" className={LABEL}>
              Environment
            </label>
            <Select
              id="environment"
              className="w-32 shrink-0 sm:w-40"
              tone={environment?.production === true ? "production" : undefined}
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
            </Select>
          </>
        )}
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <Button variant="outline" onClick={onSearch} aria-keyshortcuts="Control+K /">
          Search <kbd className="hidden font-mono text-xs font-normal text-muted sm:inline">Ctrl K</kbd>
        </Button>
        {/* Only the real user, as runs record them; nothing while it loads or if it cannot be read. */}
        {me.data && (
          <div className="flex items-center gap-2" aria-label={`Signed in as ${me.data.name}`}>
            <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full bg-avatar-bg text-xs font-semibold text-primary">
              {initials(me.data.name)}
            </span>
            <span className="hidden text-sm xl:inline">{me.data.name}</span>
          </div>
        )}
      </div>
    </header>
  );
}
