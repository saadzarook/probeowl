// Author: @SaadZarook
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { KillTarget } from "../api/client";
import { useSetKillSwitch } from "../api/useKillSwitches";
import { targetText } from "../killSwitches";

interface KillSwitchDialogProps {
  target: KillTarget;
  /** True to turn it on, false to turn it off. */
  on: boolean;
  onClose: () => void;
}

/** Turn a kill switch on or off: a reason is required, and both are recorded in the audit. */
export function KillSwitchDialog({ target, on, onClose }: KillSwitchDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const set = useSetKillSwitch(on);
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    set.mutate(
      { ...target, reason: reason.trim(), reference: reference.trim() || null },
      { onSuccess: () => ref.current?.close() },
    );
  }

  const title = on ? "Stop reads" : "Resume reads";
  return (
    <dialog ref={ref} className="import-dialog" aria-labelledby="kill-title" onClose={onClose} onCancel={(e) => set.isPending && e.preventDefault()}>
      <form onSubmit={submit}>
        <h2 id="kill-title">{title}</h2>
        <p>
          {on ? "Stops every read through " : "Allows reads again through "}
          {targetText(target)}.
        </p>
        {on && (
          <p className="hint">
            Runs, plan checks, schema import and live status. A lookup in flight finishes within its statement timeout, then its run stops with your reason. New runs are Skipped.
          </p>
        )}
        <label className="field">
          Reason
          <input value={reason} onChange={(e) => setReason(e.target.value)} required disabled={set.isPending} />
        </label>
        <label className="field">
          Reference (optional)
          <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={64} disabled={set.isPending} />
        </label>
        <p className="hint">Recorded in the audit with your name.</p>
        {set.isError && (
          <p role="alert" className="form-error">
            {set.error.message}
          </p>
        )}
        <div className="dialog-actions">
          <button className="btn secondary" type="button" disabled={set.isPending} onClick={() => ref.current?.close()}>
            Cancel
          </button>
          <button className="btn" type="submit" disabled={reason.trim() === "" || set.isPending}>
            {set.isPending ? "Saving…" : title}
          </button>
        </div>
      </form>
    </dialog>
  );
}
