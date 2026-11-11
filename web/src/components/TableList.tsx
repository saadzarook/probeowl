// Author: @SaadZarook
import type { SchemaResult } from "../api/client";
import { dialectOf, effectiveLabel, formatRows, importedTable, labelTitle, rowKey, startingColumns, timeColumn, usedByText, type TableRow } from "../catalog";

interface TableListProps {
  rows: TableRow[];
  schemas: Map<string, SchemaResult>;
  selected: TableRow | undefined;
  onSelect: (row: TableRow) => void;
}

/** The rows estimate: the latest import for this environment, else the catalog's, else "—". */
export function rowsOf(row: TableRow, schemas: Map<string, SchemaResult>): string {
  const imported = importedTable(row, schemas.get(row.connector)?.snapshot?.tables);
  return formatRows(imported?.approx_rows ?? row.confirmed?.approx_rows);
}

/** Every table with its connector, label (confirmed or suggested), time column, rows, usage and drift. */
export function TableList({ rows, schemas, selected, onSelect }: TableListProps) {
  return (
    <div className="table-scroll">
      <table className="table-list">
        <thead>
          <tr>
            <th scope="col">Table</th>
            <th scope="col">Label</th>
            <th scope="col">Time column</th>
            <th scope="col">Rows</th>
            <th scope="col">Used by</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const isSelected = selected !== undefined && rowKey(selected) === rowKey(row);
            const dialect = dialectOf(row.connector);
            const label = effectiveLabel(row);
            const drift = row.review?.drift ?? [];
            return (
              <tr key={rowKey(row)} data-selected={isSelected}>
                <td>
                  <button type="button" className="table-pick" aria-pressed={isSelected} onClick={() => onSelect(row)}>
                    <code>{row.table}</code>
                    <span className="hint">{dialect ? `${row.connector} · ${dialect}` : row.connector}</span>
                  </button>
                </td>
                <td>
                  {label && (
                    <span className="label-chip" data-label={label} data-suggested={row.confirmed === undefined}>
                      {labelTitle(label)}
                    </span>
                  )}
                  {row.confirmed === undefined && <span className="hint suggested-note"> {label ? "suggested" : "no label"}</span>}
                  {drift.length > 0 && <span className="drift-mark" title={drift.map((d) => `${d.environment}: ${d.message}`).join("\n")}>drift</span>}
                </td>
                <td><code>{timeColumn({ label, retention: row.confirmed?.retention, columns: startingColumns(row) })}</code></td>
                <td><code>{rowsOf(row, schemas)}</code></td>
                <td>{usedByText(row.review?.used_by)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
