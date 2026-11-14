// Author: @SaadZarook
import { useState } from "react";
import { Link } from "react-router-dom";
import { ConfirmError, type ApplicationSummary, type ColumnRole, type SchemaResult, type TableLabel } from "../api/client";
import { importCommand, useConfirmTable } from "../api/useCatalog";
import {
  dialectOf,
  importedTable,
  LABELS,
  mergeColumns,
  NO_MARKS,
  ROLE_TITLES,
  ROLES,
  sameName,
  startingDraft,
  type ColumnDraft,
  type Draft,
  toEntry,
  type TableRow,
} from "../catalog";
import type { ImportedTable } from "../followKeys";
import { ChangePreview } from "./ChangePreview";
import { CopyButton } from "./CopyButton";
import { EmptyState } from "./EmptyState";
import { LinkTargetPicker } from "./LinkTargetPicker";

interface TableDetailProps {
  row: TableRow;
  application: ApplicationSummary;
  /** The latest import of the table's connector in the current environment. */
  schema: SchemaResult | undefined;
  environmentTitle: string;
  /** The current environment's name, as the import command takes it. */
  environment: string;
  /** Every imported table of the application here, on any connector: what a link can point to. */
  targets: ImportedTable[];
  onImport: () => void;
}

/** One table: editable label, owner, description and column roles, what the review found, and the preview-then-confirm flow. */
export function TableDetail({ row, application, schema, environmentTitle, environment, targets, onImport }: TableDetailProps) {
  const [draft, setDraft] = useState(() => startingDraft(row, application.owner));
  const change = useConfirmTable(application.name);
  const imported = importedTable(row, schema?.snapshot?.tables);
  const dialect = dialectOf(row.connector);
  const label = LABELS.find((l) => l.label === draft.label);
  const columns = mergeColumns(Object.keys(draft.columns), imported);
  const name = imported?.schema ? `${imported.schema}.${row.table}` : row.table;
  const entry = toEntry(row, draft);
  const current = entry && JSON.stringify(entry);

  const preview = change.data && !change.data.written ? change.data : undefined;
  const written = change.data?.written ? change.data : undefined;
  const stale = preview !== undefined && JSON.stringify(change.variables?.entry) !== current;
  const drift = row.review?.drift ?? [];
  const importedIn = row.review?.imported_in ?? [];
  const missingIn = application.environments.filter((e) => !importedIn.includes(e.name));
  const titleOf = (environment: string) => application.environments.find((e) => e.name === environment)?.title ?? environment;

  const edit = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const editColumn = (column: string, patch: Partial<ColumnDraft>) =>
    setDraft((d) => {
      const key = Object.keys(d.columns).find((k) => sameName(k, column)) ?? column;
      return { ...d, columns: { ...d.columns, [key]: { ...(d.columns[key] ?? NO_MARKS), ...patch } } };
    });
  const toggleRole = (column: string, roles: ColumnRole[], role: ColumnRole, on: boolean) =>
    editColumn(column, { roles: on ? ROLES.filter((r) => r === role || roles.includes(r)) : roles.filter((r) => r !== role) });

  return (
    <section className="card table-detail" aria-label={`Table ${row.table}`}>
      <header>
        <h2><code>{name}</code></h2>
        <span className="hint">{dialect ? `${row.connector} · ${dialect}` : row.connector}</span>
      </header>
      <div className="facts">
        <label>
          Label
          <select value={draft.label} onChange={(e) => edit({ label: e.target.value as TableLabel | "" })}>
            {draft.label === "" && <option value="">Choose a label</option>}
            {LABELS.map((l) => <option key={l.label} value={l.label}>{l.title}</option>)}
          </select>
        </label>
        <label>
          Owner
          <input value={draft.owner} onChange={(e) => edit({ owner: e.target.value })} />
        </label>
        <label className="wide">
          Description
          <input value={draft.description} onChange={(e) => edit({ description: e.target.value })} />
        </label>
        {row.suggestion && row.suggestion.reasons.length > 0 && (
          <p className="hint wide">
            {row.confirmed ? "Suggested" : "Suggested because"}: {row.suggestion.reasons.join("; ")}
          </p>
        )}
      </div>
      {label && (
        <div className="label-sets">
          <strong>What confirming {label.title} does</strong>
          <p>{label.sets}</p>
        </div>
      )}
      <h3>Columns</h3>
      {imported === undefined && (
        <div role="note">
          <EmptyState
            title={`No schema imported for ${row.connector} in ${environmentTitle}`}
            message="Import it to list its tables and suggest a label for each. Until then, only the marked columns are shown."
          >
            <span className="empty-command">
              <code>{importCommand(application.name, row.connector, environment)}</code>
              <CopyButton text={importCommand(application.name, row.connector, environment)} label="Copy command" />
            </span>
            <button className="btn secondary" type="button" onClick={onImport}>Import from replica</button>
          </EmptyState>
        </div>
      )}
      <ul className="columns">
        {columns.map((column) => {
          const marks = Object.entries(draft.columns).find(([k]) => sameName(k, column.name))?.[1] ?? NO_MARKS;
          return (
            <li key={column.name}>
              <span><code>{column.name}</code>{column.type && <span className="hint block">{column.type}</span>}</span>
              <div className="role-editor">
                <fieldset className="role-boxes">
                  <legend className="sr-only">Roles of {column.name}</legend>
                  {ROLES.map((role) => (
                    <label key={role} className="role-box">
                      <input
                        type="checkbox"
                        checked={marks.roles.includes(role)}
                        onChange={(e) => toggleRole(column.name, marks.roles, role, e.target.checked)}
                      />
                      {ROLE_TITLES[role]}
                    </label>
                  ))}
                </fieldset>
                {marks.roles.includes("time") && (
                  <label className="role-box">
                    <input type="checkbox" checked={marks.indexed} onChange={(e) => editColumn(column.name, { indexed: e.target.checked })} />
                    Indexed
                  </label>
                )}
                {marks.roles.includes("status") && (
                  <label className="role-field">
                    Values of {column.name}
                    <input value={marks.values} placeholder="pending, succeeded" onChange={(e) => editColumn(column.name, { values: e.target.value })} />
                  </label>
                )}
                {marks.roles.includes("link") && (
                  <LinkTargetPicker column={column.name} target={marks.target} tables={targets} onChange={(target) => editColumn(column.name, { target })} />
                )}
                {marks.roles.includes("external_key") && !marks.roles.includes("link") && (
                  <label className="role-field">
                    Target of {column.name}
                    <input value={marks.target} placeholder="connector" onChange={(e) => editColumn(column.name, { target: e.target.value })} />
                  </label>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="confirm">
        <div className="confirm-actions">
          <button
            className="btn secondary"
            type="button"
            disabled={entry === undefined || change.isPending}
            onClick={() => entry && change.mutate({ entry })}
          >
            Preview
          </button>
          <button
            className="btn"
            type="button"
            disabled={preview === undefined || stale || change.isPending}
            onClick={() => preview && change.variables && change.mutate({ entry: change.variables.entry, base: preview.base })}
          >
            Confirm
          </button>
        </div>
        {entry === undefined && <p className="hint">Choose a label to preview the change.</p>}
        {change.isPending && <p role="status">Working…</p>}
        {preview && <ChangePreview change={preview} stale={stale} />}
        {written && (
          <p role="status" className="written">
            Written to <code>{written.path}</code>. Review and commit it with git.
          </p>
        )}
        {change.error && (
          <div role="alert" className="error-state">
            <p>{change.error.message}</p>
            {change.error instanceof ConfirmError && change.error.status === 409 && (
              <button className="btn secondary" type="button" disabled={entry === undefined} onClick={() => entry && change.mutate({ entry })}>
                Preview again
              </button>
            )}
          </div>
        )}
      </div>
      <footer className="detail-footer">
        <p>
          <strong>Used by</strong>{" "}
          {row.review?.used_by && row.review.used_by.length > 0
            ? row.review.used_by.map((probe, i) => (
                <span key={probe}>
                  {i > 0 && ", "}
                  <Link to={`/probes?probe=${encodeURIComponent(probe)}`}>{probe}</Link>
                </span>
              ))
            : "none"}
        </p>
        {drift.length === 0 && missingIn.length === 0 && (
          <p>Matches schema in all {application.environments.length} environments</p>
        )}
        {drift.length === 0 && missingIn.length > 0 && <p>Not imported in {missingIn.map((e) => e.title).join(", ")}.</p>}
        {drift.length > 0 && (
          <ul className="drift-list">
            {drift.map((d) => (
              <li key={`${d.environment}:${d.message}`}><strong>{titleOf(d.environment)}</strong>: {d.message}</li>
            ))}
          </ul>
        )}
      </footer>
    </section>
  );
}
