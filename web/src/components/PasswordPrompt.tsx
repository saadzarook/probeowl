// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { saveSecret, SecretExistsError, testDraftConnection, type DraftConnection } from "../api/client";

interface PasswordPromptProps {
  application: string;
  connector: string;
  environment: string;
  environmentTitle: string;
  /**
   * Change password: the new one is tested on the saved binding first and replaces the saved one
   * only if it connects. Absent, it is a missing password, saved as given, and never overwrites.
   */
  change?: { type: DraftConnection["type"] };
  /** After the keychain has it: test the connection, or start the run again. */
  onSaved: () => void;
  onCancel?: () => void;
}

/**
 * One binding's password, straight to this machine's keychain: a teammate's missing one, or Change
 * password. As Add connection (#59): the password is only in this form's own state, cleared once
 * saved, never in a mutation's variables, the URL or either storage, and the mutations leave the
 * cache with the form (gcTime 0). Nothing reads a password back.
 */
export function PasswordPrompt({ application, connector, environment, environmentTitle, change, onSaved, onCancel }: PasswordPromptProps) {
  const [password, setPassword] = useState("");
  // The fixed reason the new password did not connect; the saved one is still in place.
  const [failed, setFailed] = useState<string>();
  const id = useId();
  const save = useMutation({
    gcTime: 0,
    mutationFn: () => saveSecret(application, connector, environment, password, change !== undefined),
    onSuccess: () => {
      setPassword("");
      setFailed(undefined);
      onSaved();
    },
  });
  const check = useMutation({
    gcTime: 0,
    // The saved binding as it is, with the new password: the draft test path, nothing saved.
    mutationFn: (type: DraftConnection["type"]) => testDraftConnection(application, { connector, type, environment, password, use_saved_password: false }),
    onSuccess: (result) => {
      if (result.reachable) save.mutate();
      else setFailed(result.reason ?? "it did not connect");
    },
  });
  const exists = save.error instanceof SecretExistsError;
  const busy = save.isPending || check.isPending;
  let label = change ? "Change password" : "Save password";
  if (check.isPending) label = "Testing…";
  else if (save.isPending) label = "Saving…";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password === "" || busy) return;
    setFailed(undefined);
    if (change) check.mutate(change.type);
    else save.mutate();
  }

  const title = change ? `Change the password for ${connector} in ${environmentTitle}` : `This connection needs your password for ${environmentTitle}`;
  return (
    <form className="password-prompt" aria-labelledby={`${id}-title`} onSubmit={submit}>
      <p id={`${id}-title`}>
        <strong>{title}</strong>
      </p>
      <p className="hint">
        {change
          ? "The new one is tested first, and replaces what this machine's keychain has only if it connects."
          : `${connector} has no password saved on this machine, as when its file came from a teammate. It goes to this machine's keychain only.`}
      </p>
      <label>
        Password for {connector}
        <input
          type="password"
          value={password}
          autoComplete="new-password"
          onChange={(e) => {
            setPassword(e.target.value);
            setFailed(undefined);
          }}
        />
      </label>
      <div className="wizard-nav">
        <button className="btn" type="submit" disabled={password === "" || busy}>
          {label}
        </button>
        {onCancel && (
          <button className="btn btn-outline" type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {failed !== undefined && (
        <div role="alert" className="error-state">
          <p>
            Not changed: {failed}. The saved password is still in place.
          </p>
          <p>
            If the database is down rather than the password wrong, you can save it anyway. Until it's corrected, {connector} stops
            working in {environmentTitle} if this one is wrong.
          </p>
          <button className="btn btn-outline" type="button" disabled={busy} onClick={() => save.mutate()}>
            Save anyway
          </button>
        </div>
      )}
      {exists && (
        <div role="alert" className="error-state">
          <p>A password was saved for {connector} in {environmentTitle} meanwhile. Nothing was overwritten.</p>
          <button className="btn" type="button" onClick={onSaved}>
            Use the saved one
          </button>
        </div>
      )}
      {check.isError && (
        <p role="alert" className="form-error">
          {check.error.message}
        </p>
      )}
      {save.isError && !exists && (
        <p role="alert" className="form-error">
          {save.error.message}
        </p>
      )}
    </form>
  );
}
