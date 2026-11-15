// Author: @SaadZarook
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useApplications } from "./api/useApplications";
import type { ApplicationSummary, EnvironmentSummary } from "./api/client";

interface Selection {
  applications: ApplicationSummary[];
  loading: boolean;
  /** Message from the failed applications request, if any. */
  error: string | undefined;
  retry: () => void;
  /** Undefined means "All applications" (overview screens only). */
  application: ApplicationSummary | undefined;
  environment: EnvironmentSummary | undefined;
  /** Choose an application; an environment too when something says which, such as a run. */
  selectApplication: (name: string | undefined, environmentName?: string) => void;
  selectEnvironment: (name: string) => void;
  /** Said when a reload did not reopen Production; undefined otherwise. */
  restoreNotice: string | undefined;
  dismissRestoreNotice: () => void;
}

const SelectionContext = createContext<Selection | undefined>(undefined);

interface Chosen {
  /** Undefined is an explicit choice of "All applications". */
  application: string | undefined;
  environment: string | undefined;
  /** Read back from storage on load, not chosen in this page: never opens Production. */
  restored?: boolean;
}

// The choice survives a reload, per browser. Only names are kept, never anything from a run.
const STORAGE_KEY = "probeowl.selection";

/** The choice this browser kept, or undefined when there is none or it can't be read. */
function storedChoice(): Chosen | undefined {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return undefined; // storage blocked (private window, site data off): start as if new
  }
  if (raw === null) return undefined;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return undefined;
    const { application, environment } = value as Record<string, unknown>;
    return {
      application: typeof application === "string" ? application : undefined,
      environment: typeof environment === "string" ? environment : undefined,
      restored: true,
    };
  } catch {
    return undefined; // not ours, or written by an older release: ignore it
  }
}

function keepChoice(choice: Chosen): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ application: choice.application, environment: choice.environment }));
  } catch {
    // Storage blocked: the choice still holds until the page is reloaded.
  }
}

/** Holds the chosen application and environment; the choice carries across every screen. */
export function SelectionProvider({ children }: { children: ReactNode }) {
  const query = useApplications();
  const applications = useMemo(() => query.data ?? [], [query.data]);
  // undefined until the person chooses; "all" is an explicit choice of no application.
  const [chosen, setChosenState] = useState<Chosen | undefined>(storedChoice);
  const setChosen = useCallback((choice: Chosen) => {
    setChosenState(choice);
    keepChoice(choice);
  }, []);

  const value = useMemo<Selection>(() => {
    // A kept application that no longer exists falls back to the first, as a first visit does.
    const application = !chosen
      ? applications[0]
      : chosen.application === undefined
        ? undefined
        : (applications.find((a) => a.name === chosen.application) ?? applications[0]);
    const kept = application?.environments.find((e) => e.name === chosen?.environment) ?? application?.environments[0];
    // A reload never reopens Production: as search does, it opens the first environment that isn't,
    // and says so. Only an application with nothing but Production opens there.
    const safe = application?.environments.find((e) => !e.production);
    const environment = chosen?.restored && kept?.production && safe ? safe : kept;
    const restoreNotice =
      chosen?.restored && kept?.production && environment
        ? safe
          ? `Opened in ${environment.title}; choose ${kept.title} in the top bar if you meant it.`
          : `Opened in ${environment.title}, ${application?.title ?? "this application"}'s only environment, which is Production.`
        : undefined;
    return {
      applications,
      loading: query.isPending,
      error: query.isError ? query.error.message : undefined,
      retry: () => void query.refetch(),
      application,
      environment,
      selectApplication: (name, environmentName) => setChosen({ application: name, environment: environmentName ?? environment?.name }),
      selectEnvironment: (name) => setChosen({ application: application?.name, environment: name }),
      restoreNotice,
      dismissRestoreNotice: () => setChosen({ application: application?.name, environment: environment?.name }),
    };
  }, [applications, chosen, query, setChosen]);

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

/** Read the current selection; throws if used outside the provider. */
export function useSelection(): Selection {
  const value = useContext(SelectionContext);
  if (!value) {
    throw new Error("useSelection must be used inside SelectionProvider");
  }
  return value;
}
