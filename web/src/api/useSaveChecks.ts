// Author: @SaadZarook
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { checkDraft } from "./client";

export const CHECK_DEBOUNCE_MS = 600;

/** A value that follows `value` once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/** The save checks of the draft, rerun ~600 ms after the text stops changing. */
export function useSaveChecks(application: string, probe: string, entity: string | undefined, environment: string | undefined) {
  const draft = useDebounced({ probe, entity, environment }, CHECK_DEBOUNCE_MS);
  return useQuery({
    queryKey: ["save-checks", application, draft.probe, draft.entity, draft.environment],
    queryFn: () => checkDraft(application, draft),
    // An empty draft has nothing to check, and the API refuses it (422): ask only once there is text.
    enabled: application !== "" && draft.probe.trim() !== "",
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: Infinity,
  });
}
