// Author: @SaadZarook
import { isMap, isNode, isScalar, parse, parseDocument, stringify } from "yaml";
import type { Entity } from "./api/client";

export type ProbeKindChoice = "inspect" | "validate";

/** The fields of a draft probe the left panel shows; undefined when the YAML cannot be read. */
export interface DraftFields {
  probe: string;
  title: string;
  entity: string;
  kind: string;
  owner: string;
  environments: string[] | undefined;
}

/** Whether two texts are the same but for line endings, which the editor normalizes and saving restores. */
export function sameText(a: string, b: string): boolean {
  return a.replace(/\r\n/g, "\n") === b.replace(/\r\n/g, "\n");
}

/** A starting YAML for a probe: what its summary says, with one expectation on the entity's first location. */
export function probeTemplate(entity: string, kind: ProbeKindChoice, owner: string, name: string, firstLocation: string, title: string): string {
  const expectation = `count(${firstLocation}) >= 1`;
  const body = kind === "validate" ? `steps:\n  - expect: ${expectation}\n` : `expect:\n  - ${expectation}\n`;
  return `probe: ${name}\ntitle: ${inlineYaml(title)}\nkind: ${kind}\nentity: ${entity}\nowner: ${owner}\n${body}`;
}

/** Why a probe id cannot be used, or undefined: a-z, 0-9 and _, starting with a letter, and not taken. */
export function probeIdProblem(id: string, taken: string[]): string | undefined {
  if (!/^[a-z][a-z0-9_]*$/.test(id)) return "use a-z, 0-9 and _, starting with a letter: it names the file and every run";
  return taken.includes(id) ? `a probe named ${id} already exists` : undefined;
}

function asDoc(text: string) {
  const doc = parseDocument(text);
  return doc.errors.length === 0 && isMap(doc.contents) ? doc : undefined;
}

/** Read the panel fields; undefined when the YAML is not a readable mapping. */
export function readFields(text: string): DraftFields | undefined {
  if (!asDoc(text)) return undefined;
  const data = parse(text) as Record<string, unknown>;
  const environments = Array.isArray(data["environments"]) ? data["environments"].map(String) : undefined;
  return {
    probe: typeof data["probe"] === "string" ? data["probe"] : "",
    title: typeof data["title"] === "string" ? data["title"] : "",
    entity: typeof data["entity"] === "string" ? data["entity"] : "",
    kind: typeof data["kind"] === "string" ? data["kind"] : "",
    owner: typeof data["owner"] === "string" ? data["owner"] : "",
    environments,
  };
}

/** One value as YAML on one line: a list in flow style. */
function inlineYaml(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(inlineYaml).join(", ")}]`;
  return stringify(value).trim();
}

/**
 * Set one top-level key by changing only its value's text: serializing the document again would
 * reflow the rest of the file, and the save's diff would show lines nobody edited.
 */
export function setField(text: string, key: string, value: unknown): string {
  const doc = asDoc(text);
  if (!doc || !isMap(doc.contents)) return text;
  const pair = doc.contents.items.find((item) => isScalar(item.key) && item.key.value === key);
  const range = pair && isNode(pair.value) ? pair.value.range : undefined;
  if (range) return text.slice(0, range[0]) + inlineYaml(value) + text.slice(range[1]);
  const ending = text.endsWith("\n") || text === "" ? "" : "\n";
  return `${text}${ending}${key}: ${inlineYaml(value)}\n`;
}

/** The probe's name, from its YAML. */
export function probeName(text: string): string {
  return readName(text) ?? "";
}

function readName(text: string): string | undefined {
  if (!asDoc(text)) return undefined;
  const name = (parse(text) as Record<string, unknown>)["probe"];
  return typeof name === "string" ? name : undefined;
}

export interface KeyGraphEdge {
  location: string;
  using: string;
  keyFrom: string | undefined;
}

/** The locations of an entity and where each gets its key from; the entity YAML if readable, else the loaded entity. */
export function keyGraph(entityYaml: string | undefined, loaded: Entity | undefined): KeyGraphEdge[] {
  let locations: Record<string, { using?: unknown; key_from?: unknown }> | undefined = loaded?.locations;
  if (entityYaml !== undefined && asDoc(entityYaml)) {
    const parsed = (parse(entityYaml) as { locations?: unknown }).locations;
    if (parsed !== null && typeof parsed === "object") locations = parsed as typeof locations;
  }
  return Object.entries(locations ?? {}).map(([location, spec]) => ({
    location,
    using: typeof spec?.using === "string" ? spec.using : "",
    keyFrom: typeof spec?.key_from === "string" ? spec.key_from : undefined,
  }));
}
