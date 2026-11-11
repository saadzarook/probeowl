// Author: @SaadZarook
import { useRef, useState } from "react";
import type { Supplied } from "./types";

export const MAX_FILE_BYTES = 10_000_000;

interface FileInputProps {
  /** "File A" or "File B". */
  title: string;
  label: string;
  onLabel: (label: string) => void;
  supplied: Supplied | undefined;
  onSupplied: (supplied: Supplied | undefined) => void;
  /** After a comparison: what the server says it is. */
  details: string | undefined;
}

/** One side: upload or paste. A file past the limit is refused here, before it is sent. */
export function FileInput({ title, label, onLabel, supplied, onSupplied, details }: FileInputProps) {
  const [mode, setMode] = useState<"upload" | "paste">("upload");
  const [problem, setProblem] = useState<string>();
  const input = useRef<HTMLInputElement>(null);

  function check(name: string, text: string): boolean {
    const size = new TextEncoder().encode(text).length;
    if (size > MAX_FILE_BYTES) {
      setProblem(`${name} is ${size.toLocaleString("en")} bytes; the limit is ${MAX_FILE_BYTES.toLocaleString("en")} bytes per file.`);
      onSupplied(undefined);
      return false;
    }
    setProblem(undefined);
    return true;
  }

  async function choose(file: File | undefined) {
    if (!file) return;
    // Checked before reading: a huge file should not be read into memory to find out it is huge.
    if (file.size > MAX_FILE_BYTES) {
      setProblem(`${file.name} is ${file.size.toLocaleString("en")} bytes; the limit is ${MAX_FILE_BYTES.toLocaleString("en")} bytes per file.`);
      onSupplied(undefined);
      return;
    }
    try {
      const text = await file.text();
      if (text.includes("\0")) {
        setProblem(`${file.name} looks like a binary file; only text can be compared.`);
        onSupplied(undefined);
      } else if (check(file.name, text)) {
        onSupplied({ name: file.name, text });
      }
    } catch {
      setProblem(`${file.name} could not be read.`);
      onSupplied(undefined);
    }
  }

  function paste(text: string) {
    const name = `${title} (pasted)`;
    if (check(name, text)) onSupplied(text === "" ? undefined : { name, text });
  }

  return (
    <section className="card compare-file" aria-label={title}>
      <header className="compare-file-header">
        <h2>{title}</h2>
        <div role="group" aria-label={`${title} input`} className="segmented">
          <button type="button" aria-pressed={mode === "upload"} onClick={() => setMode("upload")}>
            Upload
          </button>
          <button type="button" aria-pressed={mode === "paste"} onClick={() => setMode("paste")}>
            Paste
          </button>
        </div>
      </header>
      <label className="field">
        Label
        <input value={label} onChange={(e) => onLabel(e.target.value)} placeholder="prod" />
      </label>
      {mode === "upload" ? (
        <div className="compare-drop">
          {supplied ? (
            <div>
              <code>{supplied.name}</code>
              {details && <p className="hint">{details}</p>}
            </div>
          ) : (
            <p className="hint">Choose a file. It stays on this machine and is never stored.</p>
          )}
          <button className="btn secondary" type="button" onClick={() => input.current?.click()}>
            {supplied ? "Replace" : "Choose file"}
          </button>
          <input
            ref={input}
            className="visually-hidden"
            type="file"
            aria-label={`${title} file`}
            tabIndex={-1}
            onChange={(e) => {
              void choose(e.target.files?.[0]);
              // So choosing the same file again, changed on disk, still reads it.
              e.target.value = "";
            }}
          />
        </div>
      ) : (
        <label className="field">
          Text
          <textarea rows={8} spellCheck={false} value={supplied?.text ?? ""} onChange={(e) => paste(e.target.value)} />
        </label>
      )}
      {problem && (
        <p role="alert" className="form-error">
          {problem}
        </p>
      )}
    </section>
  );
}
