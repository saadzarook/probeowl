// Author: @SaadZarook
import type { Run, RunbookView } from "./api/client";

export type RunbookStepView = RunbookView["steps"][number];

/** The label beside a step's title. The server decides the kind and whether it may run. */
export function stepLabel(step: RunbookStepView): string {
  switch (step.kind) {
    case "read_query":
      return step.runnable ? "Read query" : "Read query · reference only";
    case "write_query":
      return "Write query · never run here";
    case "external_query":
      return `External · ${step.tool}`;
    case "manual":
      return "Manual step";
    case "probe":
      return "Probe";
  }
}

/** What applies_to says, as a person reads it: "topup · ledger missing". */
export function appliesText(entity: string, when: string): string {
  return `${entity} · ${when.replace(".", " ").replaceAll("_", " ")}`;
}

/** The header a write query is copied under; the server writes the real one when it records the copy. */
export function writeHeader(requires: string, run: Run | undefined, environment: string | undefined): string {
  const place = run ? `${environment ?? run.environment} · run ${run.id}` : "no run";
  return `-- WRITE · ${place} · needs ${requires}`;
}

/** Put text on the clipboard; false if the browser would not, so the screen can say so. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Whether a refusal is the one a person can answer: a plan that could not be read, in Production. */
export function needsPlanAcknowledgement(message: string): boolean {
  return message.includes("to run without plan check");
}

/** What a minimal runbook is built from: the application, and its first entity and probe if it has them. */
export interface RunbookExampleFor {
  application: string;
  owner: string;
  today: string;
  entity?: string;
  location?: string;
  probe?: string;
}

/** A minimal runbook to start from: suggested when a location is missing, a manual step, then the probe again. */
export function runbookExample(about: RunbookExampleFor): string {
  const entity = about.entity ?? "<entity>";
  const location = about.location ?? "<location>";
  const lines = [
    `# ${about.application}/runbooks/${entity}_${location}_missing.yaml`,
    `runbook: ${entity}_${location}_missing`,
    `title: ${location} missing for a ${entity}`,
    `application: ${about.application}`,
    `applies_to: { entity: ${entity}, when: [${location}.missing] }`,
    `owner: ${about.owner}`,
    `last_verified: ${about.today}`,
    "steps:",
    "  - kind: manual",
    "    id: check-the-source",
    "    title: Check where the record should have been written",
    "    text: Look the subject up in the system that writes it, and note what you find.",
  ];
  if (about.probe) {
    lines.push("  - kind: probe", "    id: check-again", "    title: Check again after the fix", `    probe: ${about.probe}`);
  }
  return `${lines.join("\n")}\n`;
}
