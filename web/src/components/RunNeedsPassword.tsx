// Author: @SaadZarook
import { useEffect, useRef, useState } from "react";
import { PasswordMissingError } from "../api/client";
import { useSelection } from "../selection";
import { PasswordPrompt } from "./PasswordPrompt";

/** Where the refused run was to run: the rerun is pinned to it, whatever the top bar says now. */
export interface RefusedRun {
  application: string;
  environment: string;
}

interface RunNeedsPasswordProps {
  error: Error | null;
  /** The refused request's application and environment, from its own variables. */
  request: RefusedRun | undefined;
  /** Start the refused request again, exactly as it was sent. */
  onRerun: () => void;
}

/**
 * A run refused before it existed, for a password this machine lacks (422 password-missing): the
 * prompt right there, then the same request again, with its subject, reason and reference, once the
 * keychain has it. The rerun is a run of its own: every guard, the kill switch and its own audit
 * event apply on the server. If the top bar's application or environment changed while the prompt
 * was open, nothing runs: it asks instead. Nothing else for any other error.
 */
export function RunNeedsPassword({ error, request, onRerun }: RunNeedsPasswordProps) {
  const { application, environment, applications } = useSelection();
  // Read when the password is saved, not when the prompt opened.
  const current = useRef({ application: application?.name, environment: environment?.name });
  useEffect(() => {
    current.current = { application: application?.name, environment: environment?.name };
  }, [application, environment]);
  const [moved, setMoved] = useState<Error>();

  if (!(error instanceof PasswordMissingError) || request === undefined) return null;
  const title =
    applications.find((a) => a.name === request.application)?.environments.find((e) => e.name === error.environment)?.title ??
    error.environment;
  if (moved === error) {
    return (
      <p role="status" className="password-saved-moved">
        <strong>Environment changed; run again?</strong> The password for {error.connector} in {title} is saved. Nothing was run:
        the request was for {title}, and the top bar now shows another environment.
      </p>
    );
  }

  function saved() {
    const now = current.current;
    if (now.application !== request?.application || now.environment !== request?.environment) setMoved(error ?? undefined);
    else onRerun();
  }

  return (
    <PasswordPrompt
      key={`${error.connector}/${error.environment}`}
      application={request.application}
      connector={error.connector}
      environment={error.environment}
      environmentTitle={title}
      replace={false}
      onSaved={saved}
    />
  );
}
