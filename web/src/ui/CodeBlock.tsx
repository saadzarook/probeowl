// Author: @SaadZarook
import { useState } from "react";
import { Button } from "./Button";
import { copyText } from "./clipboard";

type CodeBlockProps = {
  code: string;
  /** What Copy says, such as "Copy command". */
  copyLabel?: string;
};

/** Code that wraps at any character, with Copy beside it from 640 px and below it under that, never over it (rule 7). */
export function CodeBlock({ code, copyLabel = "Copy" }: CodeBlockProps) {
  const [copied, setCopied] = useState<boolean>();
  return (
    <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row">
      <pre className="m-0 w-full min-w-0 flex-1 whitespace-pre-wrap rounded-md bg-code-bg p-3 font-mono text-xs text-code-fg wrap-anywhere">
        <code>{code}</code>
      </pre>
      <div className="flex shrink-0 flex-col gap-1">
        <Button variant="outline" size="sm" onClick={() => void copyText(code).then(setCopied)}>
          {copyLabel}
        </Button>
        {copied === true && (
          <span className="text-xs text-muted" role="status">
            Copied.
          </span>
        )}
        {copied === false && (
          <span className="text-xs text-production-text" role="alert">
            The browser would not copy it: select the text instead.
          </span>
        )}
      </div>
    </div>
  );
}
