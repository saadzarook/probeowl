// Author: @SaadZarook
import * as Dialog from "@radix-ui/react-dialog";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { searchAll, type SearchItem } from "../api/client";
import { useReloadConfiguration } from "../api/useReloadConfiguration";
import { reloadSummary } from "../reload";
import { environmentFor, guideMatches, readRecent, rememberRecent, resultPath, withValue, type GuidePage } from "../search";
import { useSelection } from "../selection";

// The command palette (#38): Ctrl+K anywhere, or / outside a text field. Results come from
// GET /api/search, the bundled guide's own index and a fixed list of actions.

const GROUP_TITLES: Record<SearchItem["kind"], string> = {
  application: "Applications",
  entity: "Entities",
  probe: "Probes",
  location: "Locations",
  connector: "Connectors",
  runbook: "Runbooks",
  table: "Tables",
  column: "Columns",
  run: "Runs",
};

interface Option {
  key: string;
  label: string;
  detail?: string | null;
  labels?: string[];
  run: () => void;
}

interface Group {
  title: string;
  options: Option[];
  more?: number;
}

interface SearchPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Says what changed when a result opens in another environment: never silently. */
  onNotice: (notice: string) => void;
  /** Back to where focus was when it opened. */
  returnFocus: () => void;
}

/** Search everything ProbeOwl has loaded, and jump to it. */
export function SearchPalette({ open, onOpenChange, onNotice, returnFocus }: SearchPaletteProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="palette-overlay" />
        {/* Mounted only while open, so each opening starts empty, with the latest recent items. */}
        <PaletteBody close={() => onOpenChange(false)} onNotice={onNotice} returnFocus={returnFocus} />
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PaletteBody({ close, onNotice, returnFocus }: { close: () => void; onNotice: (notice: string) => void; returnFocus: () => void }) {
  const { applications, application, environment, selectApplication } = useSelection();
  const navigate = useNavigate();
  const reload = useReloadConfiguration();
  const id = useId();
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [everywhere, setEverywhere] = useState(application === undefined);
  const [active, setActive] = useState(0);
  const [recent] = useState(readRecent);

  // Searched a moment after typing stops, not on every key.
  useEffect(() => {
    const timer = setTimeout(() => setQ(typed.trim()), 150);
    return () => clearTimeout(timer);
  }, [typed]);

  const scope = everywhere ? undefined : application?.name;
  const results = useQuery({
    queryKey: ["search", q, scope ?? null],
    queryFn: () => searchAll(q, scope),
    enabled: q !== "",
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    retry: false,
  });
  // MkDocs writes the guide's index next to it; read once, the first time the palette opens.
  const guide = useQuery({
    queryKey: ["guide-index"],
    queryFn: async (): Promise<GuidePage[]> => {
      const response = await fetch("/guide/search/search_index.json");
      if (!response.ok) throw new Error("the guide's index could not be read");
      const body = (await response.json()) as { docs?: GuidePage[] };
      return body.docs ?? [];
    },
    staleTime: Infinity,
    retry: false,
  });

  function openItem(item: Pick<SearchItem, "kind" | "application" | "id" | "title">) {
    const target = item.kind === "application" ? item.id : item.application;
    if (target && target !== application?.name) {
      const app = applications.find((a) => a.name === target);
      if (app) {
        const choice = environmentFor(app, environment);
        selectApplication(app.name, choice.environment?.name);
        if (choice.notice) onNotice(choice.notice);
      }
    }
    rememberRecent(item);
    close();
    void navigate(resultPath(item));
  }

  // Built on each render from what the palette shows: a handful of groups, cheap to rebuild.
  const groups: Group[] = (() => {
    const found: Group[] = [];
    if (q === "") {
      if (recent.length > 0) {
        found.push({
          title: "Recent",
          options: recent.map((r) => ({
            key: `recent:${r.kind}:${r.application ?? ""}:${r.id}`,
            label: r.title,
            detail: r.application ? `${GROUP_TITLES[r.kind]} · ${r.application}` : GROUP_TITLES[r.kind],
            run: () => openItem(r),
          })),
        });
      }
    }
    for (const group of results.data?.groups ?? []) {
      found.push({
        title: GROUP_TITLES[group.kind],
        more: group.more,
        options: group.items.map((item) => ({
          key: `${item.kind}:${item.application ?? ""}:${item.id}`,
          label: item.title,
          detail: [item.detail, everywhere && item.application ? item.application : null].filter(Boolean).join(" · ") || null,
          labels: item.labels,
          run: () => openItem(item),
        })),
      });
    }
    const pages = guideMatches(guide.data ?? [], q);
    if (pages.length > 0) {
      found.push({
        title: "Guide",
        options: pages.map((page) => ({
          key: `guide:${page.location}`,
          label: page.title,
          run: () => {
            close();
            window.location.assign(`/guide/${page.location}`);
          },
        })),
      });
    }
    found.push({ title: "Actions", options: actions() });
    return found.filter((g) => g.options.length > 0);
  })();

  function actions(): Option[] {
    const all: Option[] = [
      { key: "action:new-entity", label: "New entity", run: () => go("/probes/new-entity") },
      { key: "action:new-probe", label: "New probe", run: () => go("/probes?new=validate") },
      { key: "action:add-connection", label: "Add connection", run: () => go("/connectors/new") },
      {
        key: "action:reload",
        label: "Reload configuration",
        run: () => {
          close();
          reload.mutate(undefined, {
            onSuccess: (report) => onNotice(reloadSummary(report).join(" ")),
            onError: (error) => onNotice(error.message),
          });
        },
      },
    ];
    const matching = q === "" ? all : all.filter((a) => a.label.toLowerCase().includes(q.toLowerCase()));
    if (q === "") return matching;
    // Filled in, never run: the person presses Inspect or Validate there.
    return [
      ...matching,
      { key: "action:inspect", label: `Inspect "${q}"`, run: () => go(withValue("/inspect", q)) },
      { key: "action:validate", label: `Validate "${q}"`, run: () => go(withValue("/validate", q)) },
    ];
  }

  function go(path: string) {
    close();
    void navigate(path);
  }

  const flat = groups.flatMap((g) => g.options);
  const current = Math.min(active, Math.max(flat.length - 1, 0));
  const optionId = (index: number) => `${id}-option-${index}`;

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current + 1) % Math.max(flat.length, 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current - 1 + flat.length) % Math.max(flat.length, 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      flat[current]?.run();
    }
  }

  let index = -1;
  return (
    <Dialog.Content
      className="palette"
      aria-describedby={`${id}-hint`}
      onCloseAutoFocus={(event) => {
        // Opened by a shortcut, not a Radix trigger: focus goes back where it was.
        event.preventDefault();
        returnFocus();
      }}
    >
      <Dialog.Title className="sr-only">Search</Dialog.Title>
      <p id={`${id}-hint`} className="sr-only">
        Type to search. Up and down arrows move through the results, Enter opens one, Escape closes.
      </p>
      <input
        className="palette-input"
        role="combobox"
        aria-label="Search"
        aria-expanded="true"
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={flat.length > 0 ? optionId(current) : undefined}
        placeholder="Search probes, entities, tables, runs, the guide…"
        autoComplete="off"
        spellCheck={false}
        value={typed}
        onChange={(e) => {
          setTyped(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKey}
      />
      <label className="palette-scope">
        <input type="checkbox" checked={everywhere} disabled={application === undefined} onChange={(e) => setEverywhere(e.target.checked)} />
        All applications{application && !everywhere ? ` (now: ${application.title})` : ""}
      </label>
      {results.isFetching && <p className="hint" aria-busy="true">Searching…</p>}
      {results.isError && (
        <div role="alert" className="error-state">
          <p>{results.error.message}</p>
          <button className="btn" type="button" onClick={() => void results.refetch()}>
            Retry
          </button>
        </div>
      )}
      {q !== "" && results.isSuccess && (results.data.groups.length === 0) && <p className="hint">Nothing in ProbeOwl matches "{q}".</p>}
      {guide.isError && q !== "" && <p className="hint">The guide's pages can't be searched here: its index wasn't found.</p>}
      <div id={`${id}-list`} role="listbox" aria-label="Results" className="palette-list">
        {groups.map((group) => (
          <div key={group.title} role="group" aria-labelledby={`${id}-${group.title}`}>
            <p id={`${id}-${group.title}`} className="palette-group" role="presentation">
              {group.title}
              {group.more ? <span className="hint"> · {group.more} more</span> : null}
            </p>
            {group.options.map((option) => {
              index += 1;
              const mine = index;
              return (
                <div
                  key={option.key}
                  id={optionId(mine)}
                  role="option"
                  aria-selected={mine === current}
                  className="palette-option"
                  onMouseMove={() => setActive(mine)}
                  onClick={() => option.run()}
                >
                  <span>{option.label}</span>
                  {option.detail && <span className="hint"> {option.detail}</span>}
                  {(option.labels ?? []).map((label) => (
                    <span key={label} className="chip">
                      {label}
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </Dialog.Content>
  );
}
