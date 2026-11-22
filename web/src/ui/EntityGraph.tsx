// Author: @SaadZarook
import dagre from "@dagrejs/dagre";
import { useId, useMemo, useRef, type KeyboardEvent } from "react";
import { Badge, type BadgeTone } from "./Badge";
import { cn } from "./cn";

// The entity graph (v0.3, #162): an entity drawn as boxes (locations) and arrows (keys), laid out
// by dagre and drawn as one SVG behind real buttons. Phase 0 chose this over React Flow for its
// keyboard, its size and the strict policy: positions are style properties set through the DOM,
// never a <style> element or a style attribute in markup. The Outline is the same graph as nested
// lists; screens show it below 1024 px, and it is what a screen reader reads at any width.

/** One location as a box. Only what the box shows: a screen decides the outcome and the fix. */
export interface GraphLocation {
  id: string;
  title: string;
  connector: string;
  /** The table, or the REST path. */
  source: string;
  cardinality: "one" | "optional" | "many";
  /** The location the key comes from has no arrow yet: shown as "No key yet". */
  root?: boolean;
  /** A fan-out: looked up again for each row of the location its key comes from. */
  fanOut?: boolean;
  outcome: { tone: BadgeTone; label: string };
  /** One line on the outcome, such as "2 rows" or "1 of 2 cards: C104577 not on the controller". */
  detail?: string;
  /** The fix the box offers, such as "Confirm label: Transaction". */
  fix?: string;
}

/** An arrow: the key one location takes from another, by its field. */
export interface GraphKey {
  from: string;
  to: string;
  field: string;
}

export interface EntityGraphProps {
  locations: GraphLocation[];
  keys: GraphKey[];
  /** The selected box's id: the graph's one tab stop. */
  selected: string;
  onSelect: (id: string) => void;
  /** The graph's name, such as "Entity graph of Top-up". */
  label: string;
}

/** A box's size, the same for every box so that dagre's columns line up. */
export const BOX = { width: 248, height: 128 } as const;

/** Box positions, left to right along the keys. */
export function layoutGraph(locations: GraphLocation[], keys: GraphKey[]): Map<string, { x: number; y: number }> {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "LR", nodesep: 32, ranksep: 176, marginx: 16, marginy: 16 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const location of locations) graph.setNode(location.id, { width: BOX.width, height: BOX.height });
  for (const key of keys) graph.setEdge(key.from, key.to);
  dagre.layout(graph);
  // dagre orders a column to cross as few arrows as it can, not in key order. The key graph is a
  // tree, so each column can take its boxes top to bottom in the Outline's order without crossing
  // any: ↓ then moves down the screen, as it moves down the Outline.
  const columns = new Map<number, string[]>();
  for (const id of outlineOrder(locations, keys)) {
    const x = graph.node(id).x;
    columns.set(x, [...(columns.get(x) ?? []), id]);
  }
  const positions = new Map<string, { x: number; y: number }>();
  for (const [x, ids] of columns) {
    const slots = ids.map((id) => graph.node(id).y).sort((a, b) => a - b);
    ids.forEach((id, i) => positions.set(id, { x: x - BOX.width / 2, y: (slots[i] ?? 0) - BOX.height / 2 }));
  }
  return positions;
}

/** Every location in the Outline's order: depth first along the keys, from the root. */
function outlineOrder(locations: GraphLocation[], keys: GraphKey[]): string[] {
  const order: string[] = [];
  const visit = (id: string) => {
    if (order.includes(id)) return;
    order.push(id);
    for (const key of keys.filter((k) => k.from === id)) visit(key.to);
  };
  for (const top of tops(locations, keys)) visit(top.id);
  for (const location of locations) visit(location.id); // one in a cycle, which a key graph has not
  return order;
}

/** The locations with no arrow into them, in order: the root first, then any with no key yet. */
function tops(locations: GraphLocation[], keys: GraphKey[]): GraphLocation[] {
  const top = locations.filter((l) => !keys.some((k) => k.to === l.id));
  return [...top.filter((l) => l.root), ...top.filter((l) => !l.root)];
}

/** The box an arrow key moves to: along the keys (← →), among siblings (↑ ↓), or the root (Home). */
export function nextBox(key: string, from: string, locations: GraphLocation[], keys: GraphKey[]): string | undefined {
  const parent = keys.find((k) => k.to === from)?.from;
  if (key === "ArrowRight") return keys.find((k) => k.from === from)?.to;
  if (key === "ArrowLeft") return parent;
  if (key === "Home") return tops(locations, keys)[0]?.id;
  const siblings = parent === undefined ? tops(locations, keys).map((l) => l.id) : keys.filter((k) => k.from === parent).map((k) => k.to);
  const at = siblings.indexOf(from);
  if (key === "ArrowDown") return siblings[at + 1];
  if (key === "ArrowUp") return at > 0 ? siblings[at - 1] : undefined;
  return undefined;
}

/** What a screen reader says after a box's name: where it reads, its key, the outcome's detail and the fix. */
export function describeBox(location: GraphLocation, locations: GraphLocation[], keys: GraphKey[]): string {
  const into = keys.find((k) => k.to === location.id);
  const parent = into && locations.find((l) => l.id === into.from);
  const key = location.root ? "the root" : into && parent ? `key ${into.field} from ${parent.title}` : "no key yet";
  const rows = location.fanOut && parent ? `${location.cardinality}, for each row of ${parent.title}` : location.cardinality;
  return [`${location.connector}, ${location.source}`, rows, key, location.detail, location.fix && `fix: ${location.fix}`]
    .filter(Boolean)
    .join("; ");
}

function keyLine(location: GraphLocation, into: GraphKey | undefined) {
  if (location.root) return "Root";
  if (!into) return "No key yet";
  return (
    <>
      Key <code className="font-mono">{into.field}</code>
    </>
  );
}

interface BoxProps {
  location: GraphLocation;
  locations: GraphLocation[];
  keys: GraphKey[];
  selected: boolean;
  onSelect: () => void;
  descriptionId: string;
  /** In the graph: a fixed size, one line each; in the Outline: the lines wrap. */
  fixed?: { left: number; top: number };
  tabIndex?: number;
  onKeyDown?: (event: KeyboardEvent) => void;
  buttonRef?: (element: HTMLButtonElement | null) => void;
}

// The button holds only the title and the outcome, so its name is what it shows (WCAG 2.5.3); its
// click area is stretched over the box. The other lines are its description, read through
// aria-describedby from a hidden copy, so a screen reader never reads them twice.
function Box({ location, locations, keys, selected, onSelect, descriptionId, fixed, tabIndex, onKeyDown, buttonRef }: BoxProps) {
  const into = keys.find((k) => k.to === location.id);
  const line = fixed ? "truncate" : "wrap-anywhere";
  return (
    <div
      data-box={location.id}
      className={cn(
        "relative flex flex-col gap-1 rounded-lg border bg-card p-3 text-left text-foreground has-[button:focus-visible]:outline-2 has-[button:focus-visible]:outline-offset-2 has-[button:focus-visible]:outline-ring",
        selected ? "border-primary ring-2 ring-ring" : "border-border",
        fixed && "absolute overflow-hidden",
      )}
      style={fixed && { left: fixed.left, top: fixed.top, width: BOX.width, height: BOX.height }}
    >
      <button
        ref={buttonRef}
        type="button"
        tabIndex={tabIndex}
        aria-pressed={selected}
        aria-describedby={descriptionId}
        onClick={onSelect}
        onKeyDown={onKeyDown}
        className="flex min-w-0 cursor-pointer items-start justify-between gap-2 border-0 bg-transparent p-0 text-left text-foreground [font:inherit] after:absolute after:inset-0 after:rounded-lg after:content-[''] focus-visible:outline-none"
      >
        <span className={cn("font-semibold", line)}>{location.title}</span>
        <Badge tone={location.outcome.tone}>{location.outcome.label}</Badge>
      </button>
      <span aria-hidden="true" className={cn("font-mono text-xs text-muted", line)}>
        {location.connector} · {location.source}
      </span>
      <span aria-hidden="true" className={cn("text-xs text-muted", line)}>
        {keyLine(location, into)} · {location.fanOut ? "each row" : location.cardinality}
        {location.detail ? ` · ${location.detail}` : ""}
      </span>
      {location.fix && (
        <span aria-hidden="true" className="mt-auto flex min-w-0 items-center gap-2 text-xs">
          <Badge tone="waiting">Fix</Badge>
          <span className={line}>{location.fix}</span>
        </span>
      )}
      <span id={descriptionId} hidden>
        {describeBox(location, locations, keys)}
      </span>
    </div>
  );
}

/** The graph: boxes are buttons with one tab stop, the selected box; arrow keys follow the keys. */
export function EntityGraph({ locations, keys, selected, onSelect, label }: EntityGraphProps) {
  const id = useId();
  const positions = useMemo(() => layoutGraph(locations, keys), [locations, keys]);
  const boxes = useRef(new Map<string, HTMLButtonElement>());
  const spots = [...positions.values()];
  const width = Math.max(0, ...spots.map((p) => p.x + BOX.width)) + 16;
  const height = Math.max(0, ...spots.map((p) => p.y + BOX.height)) + 16;
  // A selection that is not a box (one just removed) leaves the first box as the tab stop.
  const focusable = locations.some((l) => l.id === selected) ? selected : locations[0]?.id;

  function onKeyDown(event: KeyboardEvent, from: string) {
    const next = nextBox(event.key, from, locations, keys);
    if (next === undefined) return;
    event.preventDefault();
    onSelect(next);
    boxes.current.get(next)?.focus();
  }

  return (
    <div className="overflow-auto rounded-lg border border-border bg-background" data-scrolls="x">
      <div role="group" aria-label={label} aria-describedby={`${id}-how`} className="relative" style={{ width, height }}>
        <span id={`${id}-how`} hidden>
          {locations.length} locations. Arrow keys follow the keys; Home goes to the root.
        </span>
        <svg className="absolute inset-0" width={width} height={height} aria-hidden="true">
          <defs>
            <marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="fill-muted" />
            </marker>
          </defs>
          {keys.map((key) => {
            const a = positions.get(key.from);
            const b = positions.get(key.to);
            if (!a || !b) return null;
            const [x1, y1, x2, y2] = [a.x + BOX.width, a.y + BOX.height / 2, b.x, b.y + BOX.height / 2];
            const mid = (x1 + x2) / 2;
            return (
              <g key={`${key.from}-${key.to}`} data-key={key.field}>
                <path
                  d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2 - 2},${y2}`}
                  className="fill-none stroke-muted"
                  strokeWidth="1.5"
                  markerEnd={`url(#${id}-arrow)`}
                />
                <text x={x2 - 12} y={y2 - 8} textAnchor="end" className="fill-muted stroke-background font-mono text-xs" paintOrder="stroke" strokeWidth="6">
                  {key.field}
                </text>
              </g>
            );
          })}
        </svg>
        {locations.map((location) => {
          const p = positions.get(location.id);
          if (!p) return null;
          return (
            <Box
              key={location.id}
              location={location}
              locations={locations}
              keys={keys}
              selected={location.id === selected}
              onSelect={() => onSelect(location.id)}
              descriptionId={`${id}-${location.id}`}
              fixed={{ left: p.x, top: p.y }}
              tabIndex={location.id === focusable ? 0 : -1}
              onKeyDown={(event) => onKeyDown(event, location.id)}
              buttonRef={(element) => {
                if (element) boxes.current.set(location.id, element);
                else boxes.current.delete(location.id);
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

function OutlineItem({ location, props, describedBy }: { location: GraphLocation; props: EntityGraphProps; describedBy: string }) {
  const { locations, keys, selected, onSelect } = props;
  const children = keys.filter((k) => k.from === location.id);
  return (
    <li className="grid gap-2">
      <Box
        location={location}
        locations={locations}
        keys={keys}
        selected={location.id === selected}
        onSelect={() => onSelect(location.id)}
        descriptionId={`${describedBy}-${location.id}`}
      />
      {children.length > 0 && (
        <ul className="m-0 grid list-none gap-2 border-l-2 border-border pl-4" aria-label={`From ${location.title}`}>
          {children.map((key) => {
            const child = locations.find((l) => l.id === key.to);
            return child ? <OutlineItem key={child.id} location={child} props={props} describedBy={describedBy} /> : null;
          })}
        </ul>
      )}
    </li>
  );
}

/** The Outline: the same graph as nested lists in key order, each box a real button. */
export function EntityOutline(props: EntityGraphProps) {
  const id = useId();
  return (
    <ul className="m-0 grid list-none gap-2 p-0" aria-label={props.label}>
      {tops(props.locations, props.keys).map((location) => (
        <OutlineItem key={location.id} location={location} props={props} describedBy={id} />
      ))}
    </ul>
  );
}
