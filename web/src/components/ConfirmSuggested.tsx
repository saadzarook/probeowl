// Author: @SaadZarook
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { confirmTable, type ApplicationSummary, type EnvironmentSummary } from "../api/client";
import { useCatalog, useCatalogReview, useImportSchema } from "../api/useCatalog";
import { buildRows, labelTitle, suggestedEntry } from "../catalog";

interface ConfirmSuggestedProps {
  application: ApplicationSummary;
  connector: string;
  environment: EnvironmentSummary;
}

/** After a connection is added: import its schema, then confirm every suggested label at once, or one by one on Tables. */
export function ConfirmSuggested({ application, connector, environment }: ConfirmSuggestedProps) {
  const client = useQueryClient();
  const run = useImportSchema(application.name, environment.name);
  const catalog = useCatalog(application.name);
  const review = useCatalogReview(application.name);
  const rows = buildRows(catalog.data?.tables ?? [], review.data).filter((r) => r.connector === connector);
  const suggested = rows.filter((r) => r.confirmed === undefined && r.suggestion);

  // Each table as every confirmation is: a preview, then exactly that, one after another.
  const confirmAll = useMutation({
    mutationFn: async () => {
      const done: string[] = [];
      for (const row of suggested) {
        const entry = suggestedEntry(row, application.owner);
        if (!entry) continue;
        const preview = await confirmTable(application.name, entry);
        await confirmTable(application.name, entry, preview.base);
        done.push(row.table);
      }
      return done;
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["catalog", application.name] });
      void client.invalidateQueries({ queryKey: ["catalog-review", application.name] });
    },
  });

  return (
    <section className="confirm-suggested" aria-labelledby="import-step">
      <h3 id="import-step">Its tables</h3>
      <p className="hint">The import reads {connector}'s tables, columns and indexes in {environment.title}, never data, and suggests a label for each.</p>
      {!run.data && (
        <button className="btn" type="button" disabled={run.isPending} onClick={() => run.mutate(connector)}>
          {run.isPending ? "Importing…" : `Import ${connector}'s tables`}
        </button>
      )}
      {run.isError && (
        <div role="alert" className="error-state">
          <p>{run.error.message}</p>
          <button className="btn" type="button" onClick={() => run.mutate(connector)}>Retry</button>
        </div>
      )}
      {run.data && (
        <>
          <p role="status">
            Imported {run.data.tables.length} {run.data.tables.length === 1 ? "table" : "tables"}.
          </p>
          {review.isPending || catalog.isPending ? (
            <p aria-busy="true">Reading the suggestions…</p>
          ) : suggested.length === 0 ? (
            <p className="hint">Nothing to confirm here: label its tables on Tables.</p>
          ) : (
            <>
              <ul className="suggested-list">
                {suggested.map((row) => (
                  <li key={row.table}>
                    <code>{row.table}</code> · {row.suggestion ? labelTitle(row.suggestion.label) : ""}
                    {row.suggestion && row.suggestion.reasons.length > 0 && <span className="hint block">{row.suggestion.reasons.join("; ")}</span>}
                  </li>
                ))}
              </ul>
              {!confirmAll.data && (
                <div className="wizard-nav">
                  <button className="btn" type="button" disabled={confirmAll.isPending} onClick={() => confirmAll.mutate()}>
                    {confirmAll.isPending ? "Confirming…" : `Confirm all ${suggested.length} as suggested`}
                  </button>
                  <RouterLink className="btn btn-outline" to="/tables">Review them on Tables</RouterLink>
                </div>
              )}
            </>
          )}
          {confirmAll.data && (
            <p role="status" className="written">
              Confirmed {confirmAll.data.length} in <code>{application.name}/catalog.yaml</code>. Review and commit it with git.
            </p>
          )}
          {confirmAll.isError && (
            <div role="alert" className="error-state">
              <p>{confirmAll.error.message}</p>
              <RouterLink to="/tables">Finish on Tables</RouterLink>
            </div>
          )}
        </>
      )}
    </section>
  );
}
