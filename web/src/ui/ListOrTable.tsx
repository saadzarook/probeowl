// Author: @SaadZarook
import type { Key, ReactNode } from "react";
import { useMediaQuery } from "./useMediaQuery";

export type Column<Row> = { header: string; cell: (row: Row) => ReactNode };

type ListOrTableProps<Row> = {
  /** The table's caption, and the list's name. */
  label: string;
  rows: readonly Row[];
  columns: readonly Column<Row>[];
  rowKey: (row: Row) => Key;
  /** What to say when there are no rows. */
  empty: ReactNode;
};

/** Rows as a table from 768 px, and as a list of cards below it, so nothing scrolls sideways (rule 6). */
export function ListOrTable<Row>({ label, rows, columns, rowKey, empty }: ListOrTableProps<Row>) {
  // One of the two, never both hidden in the page: a screen reader and a test each find one.
  const wide = useMediaQuery("(min-width: 768px)");
  if (rows.length === 0) return <p className="m-0 text-sm text-muted">{empty}</p>;
  if (wide) {
    return (
      <table className="w-full border-collapse text-sm text-foreground">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.header} scope="col" className="border-b border-border px-4 py-2 text-left text-xs font-semibold text-muted">
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.header} className="border-b border-border px-4 py-2 align-top">
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return (
    <ul aria-label={label} className="m-0 flex list-none flex-col gap-2 p-0">
      {rows.map((row) => (
        <li key={rowKey(row)} className="rounded-lg border border-border bg-card p-3">
          <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm text-foreground">
            {columns.map((column) => (
              <div key={column.header} className="contents">
                <dt className="text-xs font-semibold text-muted">{column.header}</dt>
                <dd className="m-0 min-w-0 [overflow-wrap:anywhere]">{column.cell(row)}</dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  );
}
