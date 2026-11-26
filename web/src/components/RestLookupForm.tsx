// Author: @SaadZarook
import { useState } from "react";
import { Button, Field, Input, Select } from "../ui";
import type { DraftLocation } from "../entityDraft";
import { restLocation, restPathProblem, type RestLink } from "../restLookup";

interface RestLookupFormProps {
  /** The selected location: its fields are the keys offered. */
  from: DraftLocation;
  rootKey: string;
  locations: DraftLocation[];
  /** The application's REST connectors. */
  connectors: string[];
  /** The catalog's links from the selected location to a REST connector. */
  links: RestLink[];
  onAdd: (added: DraftLocation, keyField: string) => void;
}

/**
 * Follow a key to a REST API: a connector, a path under its base URL, and the field whose value is
 * the last segment. Typing sends nothing: the location is added only with its button, and its first
 * lookup waits for Check sample.
 */
export function RestLookupForm({ from, rootKey, locations, connectors, links, onAdd }: RestLookupFormProps) {
  const fields = from.columns.filter((c) => c.selected).map((c) => c.field);
  const [connector, setConnector] = useState("");
  const [field, setField] = useState("");
  const [path, setPath] = useState("");
  const [touched, setTouched] = useState(false);
  const problem = restPathProblem(path);
  const chosenConnector = connector || connectors[0] || "";
  const chosenField = fields.includes(field) ? field : (fields[0] ?? "");
  const id = (part: string) => `rest-${from.name}-${part}`;

  if (connectors.length === 0) return null;
  const add = () => {
    setTouched(true);
    if (problem !== undefined || chosenConnector === "" || chosenField === "") return;
    onAdd(restLocation(chosenConnector, path, { location: from, field: chosenField }, rootKey, locations), chosenField);
    setPath("");
    setTouched(false);
  };
  const prefill = (link: RestLink) => {
    setConnector(link.connector);
    setField(fields.includes(link.from.column) ? link.from.column : (from.columns.find((c) => c.column === link.from.column)?.field ?? ""));
  };

  return (
    <fieldset className="wizard-link-by-columns" aria-label="REST lookup">
      <legend>Look the key up in a REST API</legend>
      {links.length > 0 && (
        <ul className="wizard-suggestions" aria-label="REST keys the catalog links">
          {links.map((link) => (
            <li key={`${link.from.column}>${link.connector}`}>
              <span>
                <code>
                  {link.from.location}.{link.from.column}
                </code>{" "}
                → <code>{link.connector}</code> <span className="hint">(the catalog marks it as a key of {link.connector})</span>
              </span>
              <Button variant="outline" type="button" onClick={() => prefill(link)}>
                Use {link.connector}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="hint">
        The key goes in as the last segment, encoded, with GET only and under the connector&apos;s base URL. Nothing is sent as you type: the first
        request waits for Check sample.
      </p>
      {/* One field a row: the form sits in the side column beside the graph. */}
      <div className="grid gap-2">
        <Field id={id("connector")} label="REST connector">
          <Select value={chosenConnector} onChange={(e) => setConnector(e.target.value)}>
            {connectors.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </Field>
        <Field id={id("key")} label={`Key, a field of ${from.title || from.name}`} help={fields.length === 0 ? `${from.title || from.name} exposes no field yet.` : undefined}>
          <Select value={chosenField} disabled={fields.length === 0} onChange={(e) => setField(e.target.value)}>
            {fields.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          id={id("path")}
          label="Path under the base URL"
          help={`Looked up as GET ${problem === undefined ? path.trim().replace(/\/+$/, "") : "/…"}/{{${chosenField || "key"}}}`}
          error={path !== "" || touched ? problem : undefined}
        >
          <Input value={path} placeholder="/payments" spellCheck={false} onChange={(e) => setPath(e.target.value)} />
        </Field>
      </div>
      <Button type="button" disabled={chosenField === ""} onClick={add}>
        Add the REST lookup
      </Button>
    </fieldset>
  );
}
