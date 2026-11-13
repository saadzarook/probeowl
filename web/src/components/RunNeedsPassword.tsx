// Author: @SaadZarook
import { PasswordMissingError } from "../api/client";
import { useSelection } from "../selection";
import { PasswordPrompt } from "./PasswordPrompt";

/**
 * A run refused before it existed, for a password this machine lacks (422 password-missing): the
 * prompt right there, then the same run again once the keychain has it. Nothing else for any
 * other error.
 */
export function RunNeedsPassword({ error, application, onSaved }: { error: Error | null; application: string; onSaved: () => void }) {
  const { applications } = useSelection();
  if (!(error instanceof PasswordMissingError)) return null;
  const title =
    applications.find((a) => a.name === application)?.environments.find((e) => e.name === error.environment)?.title ?? error.environment;
  return (
    <PasswordPrompt
      key={`${error.connector}/${error.environment}`}
      application={application}
      connector={error.connector}
      environment={error.environment}
      environmentTitle={title}
      replace={false}
      onSaved={onSaved}
    />
  );
}
