// Author: @SaadZarook
import { useCallback, useEffect, useState } from "react";
import { SAMPLE_DEBOUNCE_MS, type SampleState } from "../sampleRun";
import { startDraftRun, type DraftRun } from "./client";
import { useRun } from "./useRun";
import { useDebounced } from "./useSaveChecks";

/** One lookup of the sample: what to send, and a key that changes whenever the lookup would. */
export interface SampleRequest {
  key: string;
  body: DraftRun;
}

/** The sample's lookups, where they stand, and how to start one by hand. */
export interface SampleRuns {
  state: SampleState;
  /** Why the last lookup could not start or be read: a refusal, a kill switch, a run limit. */
  error?: Error;
  /** Look the sample up now: Check sample in Production, Retry after an error. */
  check: () => void;
  /** A lookup is starting or running: Check sample waits for it. */
  busy: boolean;
}

/**
 * The entity graph's lookups of its sample (#163). Live: a lookup starts once the draft has stood
 * still for a second, and at most one is in flight; a change made meanwhile is looked up when it
 * finishes. Not live (Production): only check() starts one. Each is a draft test run: guarded,
 * stored and audited like any other.
 */
export function useSampleRuns(application: string, request: SampleRequest | undefined, live: boolean): SampleRuns {
  const [runId, setRunId] = useState<string>();
  const [startedKey, setStartedKey] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<Error>();
  const run = useRun(runId);
  const settled = useDebounced(request?.key, SAMPLE_DEBOUNCE_MS);
  // A run just started has no data yet: it counts as running until its first read says otherwise.
  const running = runId !== undefined && (run.data === undefined ? !run.isError : run.data.status === "running");
  const busy = starting || running;

  const check = useCallback(() => {
    const asked = request;
    if (asked === undefined) return;
    setStarting(true);
    setError(undefined);
    setStartedKey(asked.key);
    startDraftRun(application, asked.body)
      .then((id) => setRunId(id))
      .catch((failed: unknown) => {
        setRunId(undefined);
        setError(failed instanceof Error ? failed : new Error("The sample could not be looked up."));
      })
      .finally(() => setStarting(false));
  }, [application, request]);

  // The request is rebuilt on every render; only its key says whether the lookup would change.
  useEffect(() => {
    if (!live || request === undefined || settled !== request.key) return;
    if (busy || startedKey === request.key) return;
    // Started from a timer, not the effect itself: the lookup sets state as it starts.
    const timer = setTimeout(check, 0);
    return () => clearTimeout(timer);
  }, [live, request, settled, busy, startedKey, check]);

  const readError = run.isError ? run.error : undefined;
  let state: SampleState;
  if (request === undefined) state = { kind: "idle" };
  else if (busy) state = { kind: "checking" };
  // Live, a change not looked up yet is about to be; by hand, it waits for Check sample.
  else if (startedKey !== request.key) state = live ? { kind: "checking" } : { kind: "idle" };
  else if (error === undefined && readError === undefined && run.data !== undefined) state = { kind: "done", run: run.data };
  else state = { kind: "idle" };
  const shownError = error ?? readError ?? undefined;
  return { state, check, busy, ...(shownError ? { error: shownError } : {}) };
}
