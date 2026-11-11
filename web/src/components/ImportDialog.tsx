// Author: @SaadZarook
import { useEffect, useRef, useState } from "react";
import { useImportSchema } from "../api/useCatalog";

interface ImportDialogProps {
  application: string;
  environment: { name: string; title: string };
  connectors: string[];
  onClose: () => void;
}

/** Pick a SQL connector and import its schema in the current environment; definitions only, never data. */
export function ImportDialog({ application, environment, connectors, onClose }: ImportDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const [connector, setConnector] = useState(connectors[0] ?? "");
  const run = useImportSchema(application, environment.name);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const done = run.data;
  return (
    <dialog ref={ref} className="import-dialog" aria-labelledby="import-title" onClose={onClose} onCancel={(e) => run.isPending && e.preventDefault()}>
      <h2 id="import-title">Import from replica</h2>
      <p className="hint">Reads table and column definitions from the {environment.title} binding of a SQL connector, never data.</p>
      {connectors.length === 0 ? (
        <p>This application has no SQL connector to import.</p>
      ) : (
        <>
          <label className="field">
            SQL connector
            <select value={connector} disabled={run.isPending} onChange={(e) => { run.reset(); setConnector(e.target.value); }}>
              {connectors.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <p className="hint">Environment: {environment.title}</p>
        </>
      )}
      {run.isPending && <p role="status">Importing {connector}… this can take up to 30 s per catalog query.</p>}
      {run.isError && <p role="alert" className="form-error">{run.error.message}</p>}
      {done && (
        <div role="status" className="summary">
          <p>
            Imported {done.tables.length} {done.tables.length === 1 ? "table" : "tables"} from {done.connector} in {environment.title}.
          </p>
          {done.truncated && <p>{done.truncated}</p>}
        </div>
      )}
      <div className="dialog-actions">
        <button className="btn secondary" type="button" disabled={run.isPending} onClick={() => ref.current?.close()}>
          {done ? "Close" : "Cancel"}
        </button>
        {connectors.length > 0 && (
          <button className="btn" type="button" disabled={run.isPending} onClick={() => run.mutate(connector)}>
            {run.isPending ? "Importing…" : "Import"}
          </button>
        )}
      </div>
    </dialog>
  );
}
