// Author: @SaadZarook
import { useEffect, useMemo, useRef } from "react";
import type { Compared, Range, Supplied } from "./types";

// Rendering more than this as lines makes the page crawl; the differences above stay complete.
const MAX_LINES = 2_000;
const HIDDEN = "••••••••";

/** The text with every range hidden, from the last so earlier offsets stay right. */
function masked(text: string, ranges: Range[]): string {
  let shown = text;
  for (const range of [...ranges].sort((a, b) => b.start - a.start)) {
    shown = `${shown.slice(0, range.start)}${HIDDEN}${shown.slice(range.end)}`;
  }
  return shown;
}

function Pane({ file, lines, others, target }: { file: Supplied; lines: string[]; others: ReadonlySet<string>; target: number }) {
  const line = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    line.current?.scrollIntoView({ block: "nearest" });
  }, [target]);
  return (
    <div className="compare-pane">
      <p className="hint">
        <code>{file.name}</code>
      </p>
      <pre tabIndex={0} aria-label={`Side by side: ${file.name}`}>
        {lines.slice(0, MAX_LINES).map((text, index) => (
          <span key={index} ref={index === target ? line : undefined} className="compare-line" data-differs={!others.has(text.trim())} data-target={index === target}>
            {text || " "}
          </span>
        ))}
      </pre>
    </div>
  );
}

/** The last key of a path: what to look for in the text. */
function keyOf(path: string): string {
  const parts = path.replace(/\[[^\]]*\]/g, ".").split(".").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** Both files with their secrets hidden. Withheld if the server could not locate one of them. */
export function SideBySide({ compared, selected }: { compared: Compared; selected: string | undefined }) {
  const { left, right, result } = compared;
  const hiddenLeft = result.masked_left;
  const hiddenRight = result.masked_right;
  const panes = useMemo(() => {
    if (!hiddenLeft || !hiddenRight) return undefined;
    return { left: masked(left.text, hiddenLeft).split(/\r?\n/), right: masked(right.text, hiddenRight).split(/\r?\n/) };
  }, [left.text, right.text, hiddenLeft, hiddenRight]);

  if (!panes) {
    return (
      <section className="card" aria-label="Side by side">
        <h2>Side by side</h2>
        <p role="note">Hidden: a masked value could not be located in the file, so showing the text could show it.</p>
      </section>
    );
  }
  const key = selected ? keyOf(selected) : undefined;
  const lineOf = (lines: string[]) => (key ? lines.findIndex((text) => text.includes(key)) : -1);
  const longest = Math.max(panes.left.length, panes.right.length);
  return (
    <section className="card" aria-label="Side by side">
      <header className="compare-side-header">
        <h2>
          Side by side{selected && (
            <>
              {" "}
              · <code>{selected}</code>
            </>
          )}
        </h2>
        <p className="hint">Select a path above to jump to it</p>
      </header>
      {longest > MAX_LINES && <p className="hint">Showing the first {MAX_LINES.toLocaleString("en")} lines. The differences above are complete.</p>}
      <div className="compare-panes">
        <Pane file={left} lines={panes.left} others={new Set(panes.right.map((t) => t.trim()))} target={lineOf(panes.left)} />
        <Pane file={right} lines={panes.right} others={new Set(panes.left.map((t) => t.trim()))} target={lineOf(panes.right)} />
      </div>
    </section>
  );
}
