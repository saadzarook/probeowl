// Author: @SaadZarook
import type { Run } from "../api/client";
import { cliCommand } from "../runFormat";
import { CopyButton } from "./CopyButton";

/** The owl command that runs this probe on this subject again, to copy. */
export function CliCommand({ run }: { run: Run }) {
  const command = cliCommand(run);
  return (
    <p className="cli-command">
      <code>{command}</code> <CopyButton text={command} label="Copy command" />
    </p>
  );
}
