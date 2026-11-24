// Author: @SaadZarook
import { keepPreviousData, queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { saveEntity, SaveError, type FieldError, type SaveCheckReport } from "./client";
import { CHECK_DEBOUNCE_MS, useDebounced } from "./useSaveChecks";

/** A draft entity's save checks: the report when it passes, or each failure when it is refused. */
export interface EntityCheck {
  report?: SaveCheckReport;
  failures: FieldError[];
  /** What was checked: an answer about an earlier draft says nothing about this one. */
  of: { yaml: string; environment: string; catalogVersion: string };
}

/** The draft to check, where, and the catalog it is checked against, by its content. */
interface CheckedDraft {
  entity: string;
  yaml: string;
  environment: string;
  catalogVersion: string;
}

const checkOf = (application: string, draft: CheckedDraft) =>
  queryOptions({
    queryKey: ["entity-check", application, draft.entity, draft.yaml, draft.environment, draft.catalogVersion],
    queryFn: async (): Promise<EntityCheck> => {
      const of = { yaml: draft.yaml, environment: draft.environment, catalogVersion: draft.catalogVersion };
      try {
        const change = await saveEntity(application, draft.entity, { entity: draft.yaml, environment: draft.environment, save_without_plan_check: false });
        return { report: change.report, failures: [], of };
      } catch (error) {
        if (error instanceof SaveError && error.status === 422) {
          return { failures: error.errors.length > 0 ? error.errors : [{ field: "entity", message: error.message }], of };
        }
        throw error;
      }
    },
    retry: false,
    staleTime: Infinity,
  });

/**
 * A draft entity's save checks. They read query plans, so live decides when they run: live (outside
 * Production), once the draft stops changing and whenever the catalog does; otherwise only when
 * run() is called, never by an edit. A preview: nothing is written. A refusal (422) is an answer,
 * not an error; anything else is an error to retry.
 */
export function useEntityCheck(application: string, current: CheckedDraft, enabled: boolean, live: boolean) {
  const client = useQueryClient();
  const settled = useDebounced(current, CHECK_DEBOUNCE_MS);
  const draft = live ? settled : current;
  const query = useQuery({
    ...checkOf(application, draft),
    enabled: enabled && live && draft.yaml === current.yaml && draft.entity === current.entity,
    // Another draft's answer is kept on screen only while its successor is already on its way.
    ...(live ? { placeholderData: keepPreviousData } : {}),
  });
  /** Run them now on the draft as it is; the answer shows as the query's too. */
  const run = () => client.fetchQuery({ ...checkOf(application, current), staleTime: 0 });
  /** What Create's own preview found, so it shows on the locations as a check's answer does. */
  const record = (answer: Omit<EntityCheck, "of">) => {
    const of = { yaml: current.yaml, environment: current.environment, catalogVersion: current.catalogVersion };
    client.setQueryData(checkOf(application, current).queryKey, { ...answer, of });
  };
  return { ...query, run, record };
}
