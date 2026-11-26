// Author: @SaadZarook
import { Button, Field, FormGrid, Input, Select } from "../ui";
import { useState } from "react";
import type { CatalogTable } from "../api/client";
import { DEFAULT_MAX_FANOUT, DEFAULT_MAX_ROWS, isRest, sourceText, type DraftLocation } from "../entityDraft";
import { linkedLocation, reasonText, suggestedLinks, tableReference, type ImportedTable, type Link } from "../followKeys";
import { linkFix, masked, type Failure } from "../locationChecks";
import { restLinks } from "../restLookup";
import { LocationFailures } from "./LocationFailures";
import { RestLookupForm } from "./RestLookupForm";

/** Where the draft's save checks stand, shown with the locations. */
export interface ChecksState {
  checking: boolean;
  /** Not run on this draft yet, and why: in Production they wait for a click. */
  waiting?: string;
  byLocation: ReadonlyMap<string, Failure[]>;
  general: Failure[];
  /** The checks could not run: the message, with a retry. */
  error?: string;
  onRetry: () => void;
}

interface EntityLocationsProps {
  application: string;
  locations: DraftLocation[];
  rootKey: string;
  tables: ImportedTable[];
  catalog: CatalogTable[];
  rowCeilings: ReadonlyMap<string, number>;
  /** Fields other parts of the draft use, by location: they stay selected. */
  inUse: ReadonlyMap<string, Set<string>>;
  checks: ChecksState;
  onChange: (locations: DraftLocation[]) => void;
}

/** The entity's locations, each with its save checks. */
export function EntityLocations({ application, locations, rootKey, tables, catalog, rowCeilings, inUse, checks, onChange }: EntityLocationsProps) {
  const update = (name: string, change: Partial<DraftLocation>) => onChange(locations.map((l) => (l.name === name ? { ...l, ...change } : l)));
  const keyedFrom = (name: string) => locations.filter((l) => l.keyFrom?.location === name).map((l) => l.name);

  return (
    <section className="card" aria-labelledby="added-locations">
      <h2 id="added-locations">Locations</h2>
      <ChecksStatus checks={checks} />
      <ul className="wizard-locations">
        {locations.map((location, index) => {
          const used = inUse.get(location.name) ?? new Set<string>();
          const dependants = keyedFrom(location.name);
          const ceiling = rowCeilings.get(location.connector);
          const failures = checks.byLocation.get(location.name) ?? [];
          return (
            <li key={location.name} aria-label={`Location ${location.name}`}>
              <header className="wizard-location-header">
                <strong>{location.name}</strong>
                <span className="hint">
                  {location.connector} · {isRest(location) ? sourceText(location, rootKey) : `${location.table} · by ${location.keyColumn}`}
                  {location.keyFrom ? ` from ${location.keyFrom.location}.${location.keyFrom.field}` : index === 0 ? " (the root key)" : ` = ${rootKey}`}
                </span>
                {index > 0 && (
                  <Button
                    variant="outline"
                    type="button"
                    disabled={dependants.length > 0}
                    title={dependants.length > 0 ? `${dependants.join(", ")} is keyed from it` : undefined}
                    onClick={() => onChange(locations.filter((l) => l.name !== location.name))}
                  >
                    Remove {location.name}
                  </Button>
                )}
              </header>
              {index > 0 && (
                <FormGrid className="xl:grid-cols-2">
                  <Field id={`${location.name}-cardinality`} label="Records per subject">
                    <Select value={location.cardinality} onChange={(e) => update(location.name, e.target.value === "many" ? { cardinality: "many", maxRows: Math.min(DEFAULT_MAX_ROWS, ceiling ?? DEFAULT_MAX_ROWS) } : { cardinality: "one", maxRows: undefined })}>
                      <option value="one">One</option>
                      <option value="many">Many</option>
                    </Select>
                  </Field>
                  {location.maxFanout !== undefined && (
                    <Field id={`${location.name}-max-fanout`} label={`Most rows of ${location.keyFrom?.location ?? ""} it is looked up for (max_fanout)`}>
                      <Input type="number" min={1} value={location.maxFanout} onChange={(e) => update(location.name, { maxFanout: Math.max(1, Number(e.target.value) || DEFAULT_MAX_FANOUT) })} />
                    </Field>
                  )}
                  {location.cardinality === "many" && location.maxFanout === undefined && (
                    <Field id={`${location.name}-max-rows`} label={`Most rows (max_rows${ceiling ? `, at most ${ceiling}` : ""})`}>
                      <Input type="number" min={1} max={ceiling} value={location.maxRows ?? DEFAULT_MAX_ROWS} onChange={(e) => update(location.name, { maxRows: Number(e.target.value) || DEFAULT_MAX_ROWS })} />
                    </Field>
                  )}
                </FormGrid>
              )}
              <LocationFailures
                application={application}
                location={location.name}
                failures={failures}
                linkFix={linkFix(location, failures, catalog, tables)}
                rowCeiling={ceiling}
                onLowerMaxRows={(maxRows) => update(location.name, { maxRows })}
                onMaskPii={() => update(location.name, { columns: masked(location, catalog, tables).columns })}
              />
              <fieldset className="wizard-columns">
                <legend className="mb-1 p-0 text-sm font-medium text-foreground">{isRest(location) ? "Fields to show (exposes)" : "Columns to select"}</legend>
                {isRest(location) && (
                  <p className="hint">
                    {location.columns.length === 0
                      ? "Its fields show here once the sample is looked up."
                      : "Only ticked fields are shown. Every other field of the answer is masked, in every run."}
                  </p>
                )}
                {location.columns.map((c) => {
                  const locked = (!isRest(location) && c.column === location.keyColumn) || used.has(c.field);
                  return (
                    <label key={c.column} title={locked ? "Used by the key graph or an identifier" : undefined}>
                      <input
                        type="checkbox"
                        checked={c.selected}
                        disabled={locked}
                        onChange={(e) => update(location.name, { columns: location.columns.map((x) => (x.column === c.column ? { ...x, selected: e.target.checked } : x)) })}
                      />
                      {c.field === c.column ? c.column : `${c.column} as ${c.field}`}
                      {c.pii && <span className="pii-tag"> PII</span>}
                    </label>
                  );
                })}
              </fieldset>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

interface FollowKeysProps extends Omit<EntityLocationsProps, "application" | "inUse" | "checks"> {
  /** The selected box: the tables offered share a key with it. */
  selected: DraftLocation;
  /** The application's REST connectors, which a key can be looked up in. */
  restConnectors: string[];
}

/** Follow a key: the tables that share a key with the selected location, and a link chosen by hand. */
export function FollowKeys({ locations, selected, rootKey, tables, catalog, rowCeilings, restConnectors, onChange }: FollowKeysProps) {
  const suggestions = suggestedLinks(locations, tables, catalog).filter((s) => s.from.location === selected.name);
  const title = selected.title || selected.name;
  const add = (link: Link) => {
    const from = locations.find((l) => l.name === link.from.location);
    if (!from) return;
    const added = linkedLocation(link, from, rootKey, tables, catalog, locations, rowCeilings.get(link.to.connector));
    if (!added) return;
    // The column the new location is keyed from must be one its location exposes.
    const exposed = locations.map((l) => (l.name === from.name ? { ...l, columns: l.columns.map((c) => (c.column === link.from.column ? { ...c, selected: true } : c)) } : l));
    onChange([...exposed, added]);
  };
  const addRest = (added: DraftLocation, field: string) => {
    // As for a table: the field the new location is keyed from is exposed by its location.
    const exposed = locations.map((l) => (l.name === selected.name ? { ...l, columns: l.columns.map((c) => (c.field === field ? { ...c, selected: true } : c)) } : l));
    onChange([...exposed, added]);
  };

  return (
    <section className="card" aria-labelledby="follow-keys">
      <h2 id="follow-keys">Follow a key</h2>
      <p className="hint">
        From <strong>{title}</strong>: tables imported in this environment, on any connector, that share a key with it. Select another box for
        its own. Common names such as id or status are offered only when the catalog links them; choose those yourself below.
      </p>
      {suggestions.length === 0 ? (
        <p>{selected.maxFanout !== undefined ? `${title} is looked up for each row, so nothing can be keyed from it.` : `No table shares a key with ${title}. Link one by its columns below.`}</p>
      ) : (
        <ul className="wizard-suggestions">
          {suggestions.map((s) => (
            <li key={`${s.from.location}.${s.from.column}>${s.to.connector}.${s.to.table}.${s.to.column}`}>
              <span>
                <code>
                  {s.from.location}.{s.from.column}
                </code>{" "}
                →{" "}
                <code>
                  {s.to.connector}.{s.to.table}.{s.to.column}
                </code>{" "}
                <span className="hint">({reasonText(s, selected.connector)})</span>
              </span>
              <Button variant="outline" type="button" onClick={() => add(s)}>
                Add {s.to.table}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <LinkByColumns locations={locations} tables={tables} onLink={add} />
      {selected.maxFanout === undefined && (
        <RestLookupForm
          key={selected.name}
          from={selected}
          rootKey={rootKey}
          locations={locations}
          connectors={restConnectors}
          links={restLinks(selected, catalog, restConnectors)}
          onAdd={addRest}
        />
      )}
    </section>
  );
}

/** Whether the draft's locations pass their save checks: checking, passed, failed (on the locations below), or not run. */
function ChecksStatus({ checks }: { checks: ChecksState }) {
  if (checks.error !== undefined) {
    return (
      <div className="error-state" role="alert">
        <p>The save checks could not run: {checks.error}</p>
        <Button type="button" onClick={checks.onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  const failed = [...checks.byLocation.values()].reduce((n, f) => n + f.length, 0) + checks.general.length;
  return (
    <div aria-live="polite">
      {checks.checking ? (
        <p className="hint" aria-busy="true">Checking the locations…</p>
      ) : checks.waiting !== undefined ? (
        <p className="hint">{checks.waiting}</p>
      ) : failed === 0 ? (
        <p className="hint">Every location passes its save checks.</p>
      ) : (
        <p className="hint">
          {failed === 1 ? "1 save check fails" : `${failed} save checks fail`}: fix it below, or remove the location. Nothing is left out without you.
        </p>
      )}
      {checks.general.length > 0 && (
        <ul className="form-error" aria-label="Failed save checks">
          {checks.general.map((f) => (
            <li key={`${f.check}: ${f.message}`}>
              {f.check}: {f.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Link by columns…: a column of an added location and a column of any imported table, picked by hand. */
function LinkByColumns({ locations, tables, onLink }: { locations: DraftLocation[]; tables: ImportedTable[]; onLink: (link: Link) => void }) {
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState("");
  const [table, setTable] = useState("");
  const [column, setColumn] = useState("");
  const fromChoices = locations.filter((l) => l.maxFanout === undefined).flatMap((l) => l.columns.map((c) => `${l.name}.${c.column}`));
  const target = tables.find((t) => `${t.connector}.${t.table}` === table);
  const [fromLocation, fromColumn] = from.split(".");
  const ready = fromLocation !== undefined && fromColumn !== undefined && target !== undefined && column !== "";

  if (!open) {
    return (
      <Button variant="outline" type="button" onClick={() => setOpen(true)}>
        Link by columns…
      </Button>
    );
  }
  return (
    <fieldset className="wizard-link-by-columns">
      <legend>Link by columns</legend>
      <p className="hint">The save checks still run on the lookup this makes, the index check included.</p>
      <label>
        Column of a location
        <select value={from} onChange={(e) => setFrom(e.target.value)}>
          <option value="">Choose…</option>
          {fromChoices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <label>
        New table
        <select
          value={table}
          onChange={(e) => {
            setTable(e.target.value);
            setColumn("");
          }}
        >
          <option value="">Choose…</option>
          {tables.map((t) => (
            <option key={`${t.connector}.${t.table}`} value={`${t.connector}.${t.table}`}>
              {t.connector} · {tableReference(t, tables)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Its column
        <select value={column} onChange={(e) => setColumn(e.target.value)} disabled={!target}>
          <option value="">Choose…</option>
          {target?.columns.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <Button
        type="button"
        disabled={!ready}
        onClick={() => {
          if (!ready) return;
          onLink({ from: { location: fromLocation, column: fromColumn }, to: { connector: target.connector, table: target.table, column }, reason: "chosen" });
          setOpen(false);
          setFrom("");
          setTable("");
          setColumn("");
        }}
      >
        Add the location
      </Button>
    </fieldset>
  );
}
