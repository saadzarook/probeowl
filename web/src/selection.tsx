// Author: @SaadZarook
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
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
}

const SelectionContext = createContext<Selection | undefined>(undefined);

/** Holds the chosen application and environment; the choice carries across every screen. */
export function SelectionProvider({ children }: { children: ReactNode }) {
  const query = useApplications();
  const applications = useMemo(() => query.data ?? [], [query.data]);
  // undefined until the person chooses; "all" is an explicit choice of no application.
  const [chosen, setChosen] = useState<{ application: string | undefined; environment: string | undefined }>();

  const value = useMemo<Selection>(() => {
    const application = chosen
      ? applications.find((a) => a.name === chosen.application)
      : applications[0];
    const environment =
      application?.environments.find((e) => e.name === chosen?.environment) ?? application?.environments[0];
    return {
      applications,
      loading: query.isPending,
      error: query.isError ? query.error.message : undefined,
      retry: () => void query.refetch(),
      application,
      environment,
      selectApplication: (name, environmentName) => setChosen({ application: name, environment: environmentName ?? environment?.name }),
      selectEnvironment: (name) => setChosen({ application: application?.name, environment: name }),
    };
  }, [applications, chosen, query]);

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
