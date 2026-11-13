// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { saveSecret, SecretExistsError } from "../api/client";

interface PasswordPromptProps {
  application: string;
  connector: string;
  environment: string;
  environmentTitle: string;
  /** Change password: replace the saved one. The missing-password prompt never overwrites. */
  replace: boolean;
  /** After the keychain has it: test the connection, or start the run again. */
  onSaved: () => void;
  onCancel?: () => void;
}

/**
 * One binding's password, straight to this machine's keychain: a teammate's missing one, or Change
 * password. As Add connection (#59): the password is only in this form's own state, cleared once
 * saved, never in a mutation's variables, the URL or either storage, and the mutation leaves the
 * cache with the form (gcTime 0). Nothing reads a password back.
 */
export function PasswordPrompt({ application, connector, environment, environmentTitle, replace, onSaved, onCancel }: PasswordPromptProps) {
  const [password, setPassword] = useState("");
  const id = useId();
  const save = useMutation({
    gcTime: 0,
    mutationFn: () => saveSecret(application, connector, environment, password, replace),
    onSuccess: () => {
      setPassword("");
      onSaved();
    },
  });
  const exists = save.error instanceof SecretExistsError;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== "") save.mutate();
  }

  const title = replace ? `Change the password for ${connector} in ${environmentTitle}` : `This connection needs your password for ${environmentTitle}`;
  return (
    <form className="password-prompt" aria-labelledby={`${id}-title`} onSubmit={submit}>
      <p id={`${id}-title`}>
        <strong>{title}</strong>
      </p>
      <p className="hint">
        {replace
          ? "The new one replaces what this machine's keychain has, then the connection is tested with it."
          : `${connector} has no password saved on this machine, as when its file came from a teammate. It goes to this machine's keychain only.`}
      </p>
      <label>
        Password for {connector}
        <input type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
      </label>
      <div className="wizard-nav">
        <button className="btn" type="submit" disabled={password === "" || save.isPending}>
          {save.isPending ? "Saving…" : replace ? "Change password" : "Save password"}
        </button>
        {onCancel && (
          <button className="btn btn-outline" type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {exists && (
        <div role="alert" className="error-state">
          <p>A password was saved for {connector} in {environmentTitle} meanwhile. Nothing was overwritten.</p>
          <button className="btn" type="button" onClick={onSaved}>
            Use the saved one
          </button>
        </div>
      )}
      {save.isError && !exists && (
        <p role="alert" className="form-error">
          {save.error.message}
        </p>
      )}
    </form>
  );
}
