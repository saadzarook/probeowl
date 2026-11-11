// Author: @SaadZarook
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, problemError } from "@probeowl/plugin-web";
import { DifferencesTable } from "./DifferencesTable";
import { download, toCsv, toMarkdown } from "./exports";
import { FileInput } from "./FileInput";
import { SideBySide } from "./SideBySide";
import type { Compared, CompareOptions, CompareRequest, Supplied } from "./types";
import "./compare-files.css";

const FORMATS: { value: CompareRequest["format"]; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "json", label: "JSON" },
  { value: "yaml", label: "YAML" },
  { value: "csv", label: "CSV" },
  { value: "xml", label: "XML" },
  { value: "properties", label: ".properties" },
  { value: "ini", label: ".ini" },
  { value: "env", label: ".env" },
  { value: "text", label: "Plain text" },
];

async function compareFiles(body: CompareRequest) {
  const { data, error } = await api.POST("/api/plugins/compare_files/compare", { body });
  if (data) return data;
  throw problemError(error, "The files could not be compared.");
}

function size(bytes: number): string {
  return bytes < 1_000_000 ? `${(bytes / 1000).toFixed(1)} KB` : `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** Compare two files a person uploads or pastes. They stay on this machine, and are never stored. */
export function CompareFilesPage() {
  const [left, setLeft] = useState<Supplied>();
  const [right, setRight] = useState<Supplied>();
  const [labelLeft, setLabelLeft] = useState("");
  const [labelRight, setLabelRight] = useState("");
  const [format, setFormat] = useState<CompareRequest["format"]>("auto");
  const [arrays, setArrays] = useState<NonNullable<CompareOptions["arrays"]>>("by_position");
  const [arrayKey, setArrayKey] = useState("");
  const [ignorePaths, setIgnorePaths] = useState("");
  const [tolerance, setTolerance] = useState("0");
  const [ignoreKeyOrder, setIgnoreKeyOrder] = useState(true);
  const [ignoreWhitespace, setIgnoreWhitespace] = useState(false);
  const [ignoreCase, setIgnoreCase] = useState(false);
  const [compared, setCompared] = useState<Compared>();
  const [selected, setSelected] = useState<string>();
  const compare = useMutation({ mutationFn: compareFiles });

  const labels = { left: labelLeft.trim() || "File A", right: labelRight.trim() || "File B" };
  const needsKey = arrays === "by_key" && arrayKey.trim() === "";
  const canCompare = left !== undefined && right !== undefined && !needsKey && !compare.isPending;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!left || !right) return;
    const body: CompareRequest = {
      left,
      right,
      format,
      options: {
        ignore_paths: ignorePaths.split(",").map((p) => p.trim()).filter(Boolean),
        ignore_key_order: ignoreKeyOrder,
        arrays,
        array_key: arrays === "by_key" ? arrayKey.trim() : null,
        ignore_whitespace: ignoreWhitespace,
        ignore_case: ignoreCase,
        number_tolerance: tolerance.trim() || "0",
      },
    };
    setSelected(undefined);
    // The texts are kept with their result: Reveal's offsets only mean anything in those.
    compare.mutate(body, { onSuccess: (result) => setCompared({ left, right, result }) });
  }

  const result = compared?.result;
  const details = (file: Supplied | undefined, summary: NonNullable<typeof result>["left"] | undefined) =>
    file && summary && compared && (compared.left === file || compared.right === file)
      ? `${size(summary.size)} · detected ${summary.format} · sha256 ${summary.sha256.slice(0, 4)}…${summary.sha256.slice(-4)}`
      : undefined;

  return (
    <>
      <div className="page-header compare-header">
        <div>
          <h1>Compare files</h1>
          <p className="hint">Plugin · first-party · runs on this machine; files are never stored</p>
        </div>
        <div className="compare-actions">
          <button className="btn btn-outline" type="button" disabled={!result} onClick={() => result && download("comparison.md", "text/markdown", toMarkdown(result, labels))}>
            Export Markdown
          </button>
          <button className="btn btn-outline" type="button" disabled={!result} onClick={() => result && download("comparison.csv", "text/csv", toCsv(result, labels))}>
            Export CSV
          </button>
        </div>
      </div>

      <div className="compare-files">
        <FileInput title="File A" label={labelLeft} onLabel={setLabelLeft} supplied={left} onSupplied={setLeft} details={details(left, result?.left)} />
        <FileInput title="File B" label={labelRight} onLabel={setLabelRight} supplied={right} onSupplied={setRight} details={details(right, result?.right)} />
      </div>

      <form className="card compare-options" onSubmit={submit} aria-label="Options">
        <label className="field">
          Format
          <select value={format} onChange={(e) => setFormat(e.target.value as CompareRequest["format"])}>
            {FORMATS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Arrays
          <select value={arrays} onChange={(e) => setArrays(e.target.value as typeof arrays)}>
            <option value="by_position">By position</option>
            <option value="any_order">Ignore array order</option>
            <option value="by_key">Match by key</option>
          </select>
        </label>
        {arrays === "by_key" && (
          <label className="field">
            Key field or CSV column
            <input value={arrayKey} onChange={(e) => setArrayKey(e.target.value)} placeholder="id" required />
          </label>
        )}
        <label className="field compare-ignore">
          Ignore paths
          <input value={ignorePaths} onChange={(e) => setIgnorePaths(e.target.value)} placeholder="*.updated_at, build.number" />
        </label>
        <label className="field">
          Number tolerance
          <input value={tolerance} onChange={(e) => setTolerance(e.target.value)} inputMode="decimal" aria-describedby="tolerance-help" />
        </label>
        <p id="tolerance-help" className="hint compare-help">
          With Ignore array order, exact matches pair first and the rest pair in order, so two values within tolerance only pair if they are in the same order among those left.
        </p>
        <div className="compare-checks">
          <label>
            <input type="checkbox" checked={ignoreKeyOrder} onChange={(e) => setIgnoreKeyOrder(e.target.checked)} /> Ignore key order
          </label>
          <label>
            <input type="checkbox" checked={ignoreWhitespace} onChange={(e) => setIgnoreWhitespace(e.target.checked)} /> Ignore whitespace
          </label>
          <label>
            <input type="checkbox" checked={ignoreCase} onChange={(e) => setIgnoreCase(e.target.checked)} /> Ignore case (values, not keys)
          </label>
          <button className="btn" type="submit" disabled={!canCompare}>
            {compare.isPending ? "Comparing…" : "Compare"}
          </button>
        </div>
      </form>

      {compare.isError && (
        <div className="error-state" role="alert">
          <p>{compare.error.message}</p>
          <p className="hint">Change what is above and compare again.</p>
        </div>
      )}

      {!result && !compare.isPending && !compare.isError && <p className="hint">Choose or paste two files, then Compare.</p>}

      {result && compared && (
        <>
          <section className="card" aria-label="Differences">
            <header className="compare-summary">
              <h2>Differences</h2>
              <span className="compare-chip" data-change="changed">{result.summary.changed} changed</span>
              <span className="compare-chip" data-change="added">{result.summary.added} added</span>
              <span className="compare-chip" data-change="removed">{result.summary.removed} removed</span>
              <span className="compare-chip">{result.summary.unchanged} unchanged</span>
              <span className="hint compare-counts">
                {result.summary.masked} masked · {result.summary.ignored} ignored
              </span>
            </header>
            {result.differences.length === 0 && <p>No differences.</p>}
            {/* A new comparison starts with every value hidden again. */}
            <DifferencesTable key={result.left.sha256 + result.right.sha256 + JSON.stringify(result.summary)} compared={compared} labels={labels} onSelect={setSelected} />
          </section>
          <SideBySide compared={compared} selected={selected} />
        </>
      )}

      <p className="hint">The audit records that a comparison ran, with file names, sizes and hashes only. Up to 10 MB per file.</p>
    </>
  );
}
