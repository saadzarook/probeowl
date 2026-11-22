// Author: @SaadZarook
import type { SchemaResult } from "../api/client";
import { Badge, ListOrTable } from "../ui";
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
    <ListOrTable
      label="Tables"
      rows={rows}
      rowKey={rowKey}
      highlight={(row) => (selected !== undefined && rowKey(selected) === rowKey(row) ? "selected" : undefined)}
      empty="No tables yet."
      columns={[
        {
          header: "Table",
          cell: (row) => {
            const isSelected = selected !== undefined && rowKey(selected) === rowKey(row);
            const dialect = dialectOf(row.connector);
            return (
              <button type="button" className="grid cursor-pointer gap-1 border-0 bg-transparent p-0 text-left text-foreground" aria-pressed={isSelected} onClick={() => onSelect(row)}>
                <code className="font-mono">{row.table}</code>
                <span className="text-sm text-muted">{dialect ? `${row.connector} · ${dialect}` : row.connector}</span>
              </button>
            );
          },
        },
        {
          header: "Label",
          cell: (row) => {
            const label = effectiveLabel(row);
            const drift = row.review?.drift ?? [];
            return (
              <span className="inline-flex flex-wrap items-center gap-2">
                {label && (
                  <Badge className="label-chip" data-label={label} data-suggested={row.confirmed === undefined}>
                    {labelTitle(label)}
                  </Badge>
                )}
                {row.confirmed === undefined && <span className="text-sm text-muted">{label ? "suggested" : "no label"}</span>}
                {drift.length > 0 && (
                  <Badge tone="waiting" title={drift.map((d) => `${d.environment}: ${d.message}`).join("\n")}>
                    drift
                  </Badge>
                )}
              </span>
            );
          },
        },
        {
          header: "Time column",
          cell: (row) => <code className="font-mono">{timeColumn({ label: effectiveLabel(row), retention: row.confirmed?.retention, columns: startingColumns(row) })}</code>,
        },
        { header: "Rows", cell: (row) => <code className="font-mono">{rowsOf(row, schemas)}</code> },
        { header: "Used by", cell: (row) => usedByText(row.review?.used_by) },
      ]}
    />
  );
}
