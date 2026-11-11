// Author: @SaadZarook
import type { DraftIdentifier } from "../entityDraft";
import { freeIdentifierName, type IdentifierCandidate as Candidate } from "../followKeys";

interface EntityIdentifiersProps {
  rootKey: string;
  identifiers: DraftIdentifier[];
  candidates: Candidate[];
  onChange: (identifiers: DraftIdentifier[]) => void;
}

/** The values that find a subject: the root key always, and any field of a location the person ticks. */
export function EntityIdentifiers({ rootKey, identifiers, candidates, onChange }: EntityIdentifiersProps) {
  const root = identifiers.find((i) => i.via === undefined) ?? { name: rootKey, unique: true, pii: false, pattern: "" };
  const others = identifiers.filter((i) => i.via !== undefined);
  const chosen = (c: Candidate) => others.find((i) => i.via?.location === c.location && i.via.field === c.field);
  const set = (next: DraftIdentifier[]) => onChange([root, ...next]);
  // Unique and id-like columns are what people paste; status, dates and the like go under More columns.
  const ordered = [...candidates].sort((a, b) => rank(a) - rank(b));
  const likely = ordered.filter((c) => rank(c) < 2 || chosen(c) !== undefined);
  const rest = ordered.filter((c) => !likely.includes(c));
  const edit = (c: Candidate, change: Partial<DraftIdentifier>) =>
    set(others.map((i) => (i.via?.location === c.location && i.via.field === c.field ? { ...i, ...change } : i)));

  return (
    <section className="card" aria-labelledby="identifiers">
      <h2 id="identifiers">Identifiers</h2>
      <p className="hint">
        What a person can paste to find a subject. Unique is ticked where the column has a unique index; PII where the catalog marks it. Unique columns that are not personal data are ticked for you; untick any you do not want. A pattern is
        optional: the wizard reads no data to guess one. Only locations keyed by the root key are offered: a value found there leads to its
        subject.
      </p>
      <div className="wizard-row">
        <strong>{root.name}</strong> <span className="hint">the root key, always unique</span>
        <label>
          Pattern for {root.name}
          <input value={root.pattern} placeholder="such as ^VP\d{8}$" onChange={(e) => onChange([{ ...root, pattern: e.target.value }, ...others])} />
        </label>
      </div>
      <ul className="wizard-identifiers">{likely.map(row)}</ul>
      {rest.length > 0 && (
        <details className="more-columns" open={rest.some((c) => chosen(c) !== undefined)}>
          <summary>More columns ({rest.length})</summary>
          <ul className="wizard-identifiers">{rest.map(row)}</ul>
        </details>
      )}
    </section>
  );

  function row(c: Candidate) {
    const identifier = chosen(c);
    const label = `${c.location}.${c.field}`;
    return (
      <li key={label}>
        <label>
          <input
            type="checkbox"
            checked={identifier !== undefined}
            onChange={(e) =>
              set(
                e.target.checked
                  ? [...others, { name: freeIdentifierName(c, root.name, others), via: { location: c.location, field: c.field }, unique: c.unique, pii: c.pii, pattern: "" }]
                  : others.filter((i) => i !== identifier),
              )
            }
          />
          Find by {label}
        </label>
        {c.because && <span className="hint">unique: {c.because}</span>}
        {c.pii && !identifier && <span className="hint">personal data: masked if chosen</span>}
        {identifier && (
          <>
            <label>
              <input type="checkbox" checked={identifier.unique} onChange={(e) => edit(c, { unique: e.target.checked })} /> Unique
            </label>
            <label>
              <input type="checkbox" checked={identifier.pii} onChange={(e) => edit(c, { pii: e.target.checked })} /> PII
            </label>
            <label>
              Pattern for {c.field}
              <input value={identifier.pattern} onChange={(e) => edit(c, { pattern: e.target.value })} />
            </label>
          </>
        )}
      </li>
    );
  }
}

/** 0: unique; 1: named like an identifier (card_id, employee_no); 2: anything else. */
export function rank(c: { field: string; unique: boolean }): number {
  if (c.unique) return 0;
  return /(^|_)(id|no|number|code|ref|key)$/i.test(c.field) ? 1 : 2;
}
