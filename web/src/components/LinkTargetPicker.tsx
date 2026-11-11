// Author: @SaadZarook
import { useState } from "react";
import { formatTarget, parseTarget } from "../catalog";
import type { ImportedTable } from "../followKeys";

interface LinkTargetPickerProps {
  column: string;
  /** "connector.table.column", as the draft keeps it. */
  target: string;
  /** Every imported table of the application in this environment, on any connector. */
  tables: ImportedTable[];
  onChange: (target: string) => void;
}

const OTHER = "other";

/** Where a link column points: an imported table and one of its columns, picked; or typed, for a table not imported here. */
export function LinkTargetPicker({ column, target, tables, onChange }: LinkTargetPickerProps) {
  const parsed = parseTarget(target);
  const known = tables.find((t) => t.connector === parsed?.connector && t.table === parsed.table);
  // Typed when the person chose Other, or when the target is not an imported table (yet: imports load after the page).
  const [chose, setChose] = useState(false);
  const typing = chose || (target !== "" && known === undefined);
  const knownKey = known ? `${known.connector}.${known.table}` : "";
  const tableKey = typing ? OTHER : knownKey;

  const pickTable = (key: string) => {
    if (key === OTHER) {
      setChose(true);
      return;
    }
    setChose(false);
    const table = tables.find((t) => `${t.connector}.${t.table}` === key);
    // The table's own key first: what a link usually holds.
    const key0 = table?.primaryKey.length === 1 ? table.primaryKey[0] : table?.columns[0];
    onChange(table ? formatTarget({ connector: table.connector, table: table.table, column: key0 ?? null }) : "");
  };

  return (
    <fieldset className="role-field link-target">
      <legend>Link target of {column}</legend>
      <label>
        Table
        <select value={tableKey} onChange={(e) => pickTable(e.target.value)}>
          <option value="">Choose…</option>
          {tables.map((t) => (
            <option key={`${t.connector}.${t.table}`} value={`${t.connector}.${t.table}`}>
              {t.connector} · {t.table}
            </option>
          ))}
          <option value={OTHER}>Other, not imported here…</option>
        </select>
      </label>
      {known && !typing && (
        <label>
          Column
          <select value={parsed?.column ?? ""} onChange={(e) => onChange(formatTarget({ connector: known.connector, table: known.table, column: e.target.value }))}>
            {known.columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      )}
      {typing && (
        <label>
          Target of {column}
          <input value={target} placeholder="connector.table.column" onChange={(e) => onChange(e.target.value)} />
        </label>
      )}
    </fieldset>
  );
}
