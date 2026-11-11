// Author: @SaadZarook
import { useState } from "react";
import { useCatalog, useCatalogReview, useSchemas } from "../api/useCatalog";
import { useEntities } from "../api/useEntities";
import { EmptyState } from "../components/EmptyState";
import { ImportDialog } from "../components/ImportDialog";
import { TableDetail } from "../components/TableDetail";
import { importedTables } from "../followKeys";
import { ReviewBanner } from "../components/ReviewBanner";
import { TableList } from "../components/TableList";
import { buildRows, effectiveLabel, LABELS, rowKey } from "../catalog";
import type { NavItem } from "../routes";
import { useSelection } from "../selection";

const REST_LOOKUP = /^(GET|POST|PUT|PATCH|DELETE)\s/;

/** The table catalog: confirmed and suggested tables with the imported schema of the current environment merged in. */
export function TablesPage({ item }: { item: NavItem }) {
  const { application, environment } = useSelection();
  const catalog = useCatalog(application?.name);
  const entities = useEntities(application?.name);
  const review = useCatalogReview(application?.name);
  const tables = catalog.data?.tables ?? [];
  const rows = buildRows(tables, review.data);
  const sqlConnectors = [
    ...new Set([
      ...tables.map((t) => t.connector),
      ...(entities.data ?? []).flatMap((e) => Object.values(e.locations).filter((l) => !REST_LOOKUP.test(l.lookup)).map((l) => l.using)),
    ]),
  ];
  const imported = new Set(rows.filter((r) => environment !== undefined && r.review?.imported_in.includes(environment.name)).map((r) => r.connector));
  const schemas = useSchemas(application?.name, [...new Set(rows.map((r) => r.connector))], environment?.name, imported);
  // What a link can point to: every table imported here, on any connector.
  const targets = importedTables([...schemas.values()].flatMap((r) => (r.snapshot ? [r.snapshot] : [])));
  const [label, setLabel] = useState<string>("all");
  const [pick, setPick] = useState<string>();
  const [importing, setImporting] = useState(false);

  const shown = label === "all" ? rows : rows.filter((r) => effectiveLabel(r) === label);
  const selected = shown.find((r) => rowKey(r) === pick) ?? shown[0];

  let body;
  if (application === undefined || environment === undefined) {
    body = <EmptyState title="Choose an application" message="The catalog belongs to one application." />;
  } else if (catalog.isPending || review.isPending) {
    body = <p aria-busy="true">Loading the catalog…</p>;
  } else if (catalog.isError || review.isError) {
    body = (
      <div className="error-state" role="alert">
        <p>{catalog.error?.message ?? review.error?.message}</p>
        <button className="btn" type="button" onClick={() => void Promise.all([catalog.refetch(), review.refetch()])}>Retry</button>
      </div>
    );
  } else if (rows.length === 0) {
    // No catalog.yaml and no import yet: the first import lists the tables, and confirming one writes the file.
    body = (
      <EmptyState
        title={`No tables yet in ${application.title}`}
        message="Import a SQL connector's schema to list its tables, each with a suggested label. Confirming a label writes it to the application's catalog.yaml."
      >
        <button className="btn" type="button" onClick={() => setImporting(true)}>Import from replica</button>
      </EmptyState>
    );
  } else {
    body = (
      <>
        <ReviewBanner review={review.data} environments={application.environments} />
        <div className="filters" role="group" aria-label="Filter by label">
          {[{ label: "all", title: "All" }, ...LABELS.filter((l) => rows.some((r) => effectiveLabel(r) === l.label))].map((l) => {
            const count = l.label === "all" ? rows.length : rows.filter((r) => effectiveLabel(r) === l.label).length;
            return (
              <button key={l.label} type="button" className="filter-chip" aria-pressed={label === l.label} onClick={() => setLabel(l.label)}>
                {l.title} <span>{count}</span>
              </button>
            );
          })}
        </div>
        <div className="tables-layout">
          <TableList rows={shown} schemas={schemas} selected={selected} onSelect={(r) => setPick(rowKey(r))} />
          {selected && (
            <TableDetail
              key={rowKey(selected)}
              row={selected}
              application={application}
              schema={schemas.get(selected.connector)}
              environmentTitle={environment.title}
              environment={environment.name}
              targets={targets}
              onImport={() => setImporting(true)}
            />
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <h1>{item.label}</h1>
        {application && environment && catalog.isSuccess && rows.length > 0 && (
          <button className="btn secondary" type="button" onClick={() => setImporting(true)}>Import from replica</button>
        )}
      </div>
      {body}
      {importing && application && environment && (
        <ImportDialog
          application={application.name}
          environment={environment}
          connectors={sqlConnectors}
          onClose={() => setImporting(false)}
        />
      )}
    </>
  );
}
