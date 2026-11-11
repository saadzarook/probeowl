// Author: @SaadZarook
import { Fragment, useState } from "react";
import { shownValue } from "./exports";
import type { Compared, FileDifference, Range } from "./types";

/** A note saying an array fell back to position, and the array it is about. */
function fallbackPrefix(note: string): string | undefined {
  const match = /^(.+?): matched by position/.exec(note);
  if (!match?.[1]) return undefined;
  return match[1] === "(top level)" ? "" : match[1];
}

/** Each row's fallback note, placed before the first row it affects; the rest stay general. */
export function placeNotes(notes: string[], differences: FileDifference[]) {
  const beforeRow = new Map<number, string[]>();
  const general: string[] = [];
  for (const note of notes) {
    const prefix = fallbackPrefix(note);
    const row = prefix === undefined ? -1 : differences.findIndex((d) => d.path.startsWith(`${prefix}[`));
    if (row < 0) {
      general.push(note);
    } else {
      beforeRow.set(row, [...(beforeRow.get(row) ?? []), note]);
    }
  }
  return { beforeRow, general };
}

function CellValue({ value, revealed }: { value: FileDifference["left"]; revealed: string | undefined }) {
  if (revealed !== undefined) return <code>{revealed}</code>;
  if (value === "[masked]") return <code aria-label="masked">••••••••</code>;
  if (value === null || value === undefined) return <span className="hint">—</span>;
  return <code>{shownValue(value)}</code>;
}

interface DifferencesTableProps {
  compared: Compared;
  labels: { left: string; right: string };
  onSelect: (path: string) => void;
}

/** What differs, by path. Reveal reads the person's own file in this page: nothing is sent or logged. */
export function DifferencesTable({ compared, labels, onSelect }: DifferencesTableProps) {
  const { result } = compared;
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(new Set());
  const { beforeRow, general } = placeNotes(result.notes, result.differences);

  function toggle(path: string) {
    const next = new Set(revealed);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setRevealed(next);
  }

  const read = (text: string, range: Range | null | undefined) => (range ? text.slice(range.start, range.end) : undefined);

  return (
    <>
      {general.map((note) => (
        <p key={note} className="hint" role="note">
          {note}
        </p>
      ))}
      <div className="table-scroll compare-table">
        <table>
          <thead>
            <tr>
              <th scope="col">Path</th>
              <th scope="col">{labels.left}</th>
              <th scope="col">{labels.right}</th>
              <th scope="col">Change</th>
            </tr>
          </thead>
          <tbody>
            {result.differences.map((d, index) => {
              const left = read(compared.left.text, d.reveal_left);
              const right = read(compared.right.text, d.reveal_right);
              const canReveal = d.masked && (left !== undefined || right !== undefined);
              const open = revealed.has(d.path);
              return (
                <Fragment key={d.path}>
                  {beforeRow.get(index)?.map((note) => (
                    <tr key={note} className="compare-note">
                      <td colSpan={4} role="note">
                        {note}
                      </td>
                    </tr>
                  ))}
                  <tr data-change={d.change}>
                    <td>
                      <button type="button" className="table-pick" onClick={() => onSelect(d.path)}>
                        <code>{d.path}</code>
                      </button>
                    </td>
                    <td>
                      <CellValue value={d.left} revealed={open ? left : undefined} />
                    </td>
                    <td>
                      <CellValue value={d.right} revealed={open ? right : undefined} />
                    </td>
                    <td className="compare-change">
                      <span className="compare-chip" data-change={d.change}>
                        {d.change[0]?.toUpperCase()}
                        {d.change.slice(1)}
                      </span>
                      {canReveal && (
                        <button type="button" className="btn secondary compare-reveal" aria-pressed={open} aria-label={`${open ? "Hide" : "Reveal"} ${d.path}`} onClick={() => toggle(d.path)}>
                          {open ? "Hide" : "Reveal"}
                        </button>
                      )}
                    </td>
                  </tr>
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
