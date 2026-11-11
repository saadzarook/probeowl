// Author: @SaadZarook
import { useState } from "react";
import type { CatalogChange } from "../api/client";
import { useConfirmTable } from "../api/useCatalog";
import { PII_MASKED, ROW_CEILING, type Failure, type LinkFix } from "../locationChecks";
import { ChangePreview } from "./ChangePreview";

interface LocationFailuresProps {
  application: string;
  location: string;
  failures: Failure[];
  /** The catalog entry that fixes a refused key lookup, when the wizard knows it. */
  linkFix: LinkFix | undefined;
  rowCeiling: number | undefined;
  onLowerMaxRows: (rows: number) => void;
  onMaskPii: () => void;
}

/** A location's failed save checks, each with its fix where one is known. The location stays until the person removes it. */
export function LocationFailures({ application, location, failures, linkFix, rowCeiling, onLowerMaxRows, onMaskPii }: LocationFailuresProps) {
  if (failures.length === 0) return null;
  return (
    <section className="location-failures" aria-label={`Save checks failed for ${location}`}>
      <ul className="form-error">
        {failures.map((f) => (
          <li key={`${f.check}: ${f.message}`}>
            <strong>{f.check}</strong>: {f.message}
            {f.check === ROW_CEILING && rowCeiling !== undefined && (
              <button className="btn btn-outline" type="button" onClick={() => onLowerMaxRows(rowCeiling)}>
                Lower max_rows to {rowCeiling}
              </button>
            )}
            {f.check === PII_MASKED && (
              <button className="btn btn-outline" type="button" onClick={onMaskPii}>
                Mask the catalog's PII columns
              </button>
            )}
          </li>
        ))}
      </ul>
      {linkFix && <CatalogLinkFix application={application} fix={linkFix} />}
    </section>
  );
}

/** Mark the key column as a link in catalog.yaml: preview the change, then confirm it, as on the Tables screen. */
function CatalogLinkFix({ application, fix }: { application: string; fix: LinkFix }) {
  const confirm = useConfirmTable(application);
  const [preview, setPreview] = useState<CatalogChange>();
  const { entry, column, to } = fix;
  const text = `Mark ${entry.table}.${column} as a link to ${to.table}.${to.column}`;
  const failed = confirm.error?.message;

  if (confirm.data?.written) {
    return (
      <p role="status" className="hint">
        Marked in <code>{confirm.data.path}</code>. Checking again…
      </p>
    );
  }
  return (
    <div className="catalog-fix">
      <p>
        The catalog does not call {entry.table}.{column} a key, so a {entry.label} table may not be read by it. The wizard followed it as a link:
      </p>
      {preview === undefined ? (
        <button
          className="btn"
          type="button"
          disabled={confirm.isPending}
          onClick={() => confirm.mutate({ entry }, { onSuccess: (change) => setPreview(change) })}
        >
          {text}
        </button>
      ) : (
        <>
          <ChangePreview change={preview} stale={false} />
          <button className="btn" type="button" disabled={confirm.isPending} onClick={() => confirm.mutate({ entry, base: preview.base })}>
            Confirm in catalog.yaml
          </button>
          <button className="btn btn-outline" type="button" onClick={() => setPreview(undefined)}>
            Cancel
          </button>
        </>
      )}
      {failed && (
        <p role="alert" className="form-error">
          {failed}
        </p>
      )}
    </div>
  );
}
