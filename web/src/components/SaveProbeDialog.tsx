// Author: @SaadZarook
import { useEffect, useRef, useState } from "react";
import { SaveError, type ProbeChange } from "../api/client";
import { useSaveProbe } from "../api/useProbes";
import { ChangePreview } from "./ChangePreview";

interface SaveProbeDialogProps {
  application: string;
  probeName: string;
  probe: string;
  /** The entity's YAML, only when its tab changed. */
  entity: string | undefined;
  environment: string;
  production: boolean;
  /** The files were written: what was opened is now what was saved. */
  onSaved: () => void;
  onClose: () => void;
}

/** Preview a probe save as diffs, then confirm it; both files are written together or neither. */
export function SaveProbeDialog({ application, probeName, probe, entity, environment, production, onSaved, onClose }: SaveProbeDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const save = useSaveProbe(application, probeName);
  const [preview, setPreview] = useState<ProbeChange>();
  const [acknowledged, setAcknowledged] = useState(false);
  const [done, setDone] = useState<ProbeChange>();
  const { mutate } = save;

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
      mutate({ probe, entity, environment, save_without_plan_check: false }, { onSuccess: setPreview });
    }
  }, [mutate, probe, entity, environment]);

  /** A new preview needs its own acknowledgement: a tick never carries over to it. */
  function previewAgain() {
    setPreview(undefined);
    setAcknowledged(false);
    mutate({ probe, entity, environment, save_without_plan_check: false }, { onSuccess: setPreview });
  }

  function confirm(base: string) {
    mutate(
      { probe, entity, environment, base, save_without_plan_check: acknowledged },
      {
        onSuccess: (change) => {
          setDone(change);
          if (change.written) onSaved();
        },
      },
    );
  }

  const error = save.error instanceof SaveError ? save.error : save.error ? new SaveError(save.error.message, 0, []) : undefined;
  const conflict = error?.status === 409;
  const retryable = error !== undefined && (error.status === 404 || error.status === 501 || error.status === 0);
  const entityChange = preview?.entity_change ?? undefined;
  const notRun = preview?.report.checks.filter((c) => c.plan_not_run) ?? [];
  // The server can also ask at the write, when a plan that ran at the preview no longer can.
  const needsAcknowledgement = (production && notRun.length > 0) || error?.needsAcknowledgement === true;

  return (
    <dialog ref={ref} className="import-dialog save-dialog" aria-labelledby="save-title" onClose={onClose} onCancel={(e) => save.isPending && e.preventDefault()}>
      <h2 id="save-title">{done?.written ? "Saved" : "Save probe"}</h2>
      {done ? (
        <div role="status" className="summary">
          {done.written ? (
            <>
              <p>
                Saved <code>{done.path}</code>
                {done.entity_change?.written && (
                  <>
                    {" "}
                    and <code>{done.entity_change.path}</code>
                  </>
                )}
                .
              </p>
              <p>Saved to the config folder. Commit it when you're ready.</p>
            </>
          ) : (
            <p>Nothing changed.</p>
          )}
        </div>
      ) : (
        <>
          {save.isPending && !preview && <p role="status" aria-busy="true">Checking the change…</p>}
          {preview && (
            <>
              <p className="hint">{entityChange ? "Both files are saved together, or neither." : "Nothing is written until you confirm."}</p>
              <ChangePreview change={preview} stale={false} />
              {entityChange && <ChangePreview change={entityChange} stale={false} />}
              {notRun.length > 0 && (
                <div role="status" className="hint">
                  <p>{notRun.length === 1 ? "1 plan check" : `${notRun.length} plan checks`} could not run:</p>
                  <ul aria-label="Plan checks that could not run">
                    {notRun.map((c, i) => (
                      <li key={`${c.check}-${c.location ?? ""}-${i}`}>
                        {c.check}
                        {c.location ? ` (${c.location})` : ""}: {c.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          {error && (
            <div role="alert" className="form-error">
              <p>{conflict ? `${error.message} A file changed since the preview. Preview again to see the current change.` : error.message}</p>
              {error.errors.length > 0 && (
                <ul>
                  {error.errors.map((e, i) => (
                    <li key={`${e.field}-${i}`}>
                      {e.field}: {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {preview && needsAcknowledgement && !conflict && (
            <>
              <label className="check-option">
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                Save without plan check
              </label>
              <p className="hint">This is recorded in the audit with your name.</p>
            </>
          )}
        </>
      )}
      <div className="dialog-actions">
        <button className="btn secondary" type="button" disabled={save.isPending} onClick={() => ref.current?.close()}>
          {done ? "Close" : "Cancel"}
        </button>
        {!done && (conflict || retryable) && (
          <button className="btn" type="button" disabled={save.isPending} onClick={previewAgain}>
            {conflict ? "Preview again" : "Retry"}
          </button>
        )}
        {!done && preview && !conflict && !retryable && (
          <button className="btn" type="button" disabled={(needsAcknowledgement && !acknowledged) || save.isPending} onClick={() => confirm(preview.base)}>
            {save.isPending ? "Saving…" : "Confirm"}
          </button>
        )}
      </div>
    </dialog>
  );
}
