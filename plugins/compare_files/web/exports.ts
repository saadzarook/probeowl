// Author: @SaadZarook
import type { CompareResult, FileDifference } from "./types";

type Labels = { left: string; right: string };

function shown(value: FileDifference["left"]): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** A spreadsheet runs a cell starting with one of these as a formula, so it is made text. */
function csvCell(value: string): string {
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

function markdownCell(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
}

/** The differences as CSV: from the server's masked result, so it holds no masked value. */
export function toCsv(result: CompareResult, labels: Labels): string {
  const header = ["path", labels.left, labels.right, "change"].map(csvCell).join(",");
  const rows = result.differences.map((d) =>
    [d.path, shown(d.left), shown(d.right), d.change].map(csvCell).join(","),
  );
  return `${[header, ...rows].join("\r\n")}\r\n`;
}

/** The differences as Markdown: from the server's masked result, so it holds no masked value. */
export function toMarkdown(result: CompareResult, labels: Labels): string {
  const { summary } = result;
  const lines = [
    `# Compare files: ${markdownCell(labels.left)} and ${markdownCell(labels.right)}`,
    "",
    `${summary.changed} changed, ${summary.added} added, ${summary.removed} removed, ${summary.unchanged} unchanged. ${summary.masked} masked, ${summary.ignored} ignored.`,
    "",
  ];
  for (const note of result.notes) lines.push(`- ${note}`);
  if (result.notes.length > 0) lines.push("");
  lines.push(`| Path | ${markdownCell(labels.left)} | ${markdownCell(labels.right)} | Change |`, "| --- | --- | --- | --- |");
  for (const d of result.differences) {
    lines.push(`| ${markdownCell(d.path)} | ${markdownCell(shown(d.left))} | ${markdownCell(shown(d.right))} | ${d.change} |`);
  }
  return `${lines.join("\n")}\n`;
}

/** Hand the person a file. Built here from what is on screen: nothing is sent anywhere. */
export function download(name: string, type: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/** The value of a cell for the table. */
export { shown as shownValue };
