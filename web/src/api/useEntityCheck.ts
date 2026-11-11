// Author: @SaadZarook
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { saveEntity, SaveError, type FieldError, type SaveCheckReport } from "./client";
import { CHECK_DEBOUNCE_MS, useDebounced } from "./useSaveChecks";

/** A draft entity's save checks: the report when it passes, or each failure when it is refused. */
export interface EntityCheck {
  report?: SaveCheckReport;
  failures: FieldError[];
}

/**
 * The save checks of the wizard's draft, rerun once it stops changing and whenever the catalog does.
 * A preview: nothing is written. A refusal (422) is an answer, not an error; anything else is an error to retry.
 */
export function useEntityCheck(application: string, entity: string, yaml: string, environment: string, catalogVersion: number, enabled: boolean) {
  const draft = useDebounced({ entity, yaml, environment, catalogVersion }, CHECK_DEBOUNCE_MS);
  return useQuery({
    queryKey: ["entity-check", application, draft.entity, draft.yaml, draft.environment, draft.catalogVersion],
    queryFn: async (): Promise<EntityCheck> => {
      try {
        const change = await saveEntity(application, draft.entity, { entity: draft.yaml, environment: draft.environment, save_without_plan_check: false });
        return { report: change.report, failures: [] };
      } catch (error) {
        if (error instanceof SaveError && error.status === 422) {
          return { failures: error.errors.length > 0 ? error.errors : [{ field: "entity", message: error.message }] };
        }
        throw error;
      }
    },
    enabled: enabled && draft.yaml === yaml && draft.entity === entity,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: Infinity,
  });
}
