// Author: @SaadZarook
import type { Run } from "../api/client";
import { cliCommand } from "../runFormat";
import { CodeBlock } from "../ui";

/** The owl command that runs this probe on this subject again, to copy: it wraps, and Copy stays clear of it (rule 7). */
export function CliCommand({ run }: { run: Run }) {
  return <CodeBlock code={cliCommand(run)} copyLabel="Copy command" />;
}
