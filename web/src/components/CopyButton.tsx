// Author: @SaadZarook
import { useState } from "react";
import { copyText } from "../runbooks";

/** A button that copies text, and says whether the browser let it. */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState<boolean>();
  return (
    <>
      <button className="btn btn-outline" type="button" onClick={() => void copyText(text).then(setCopied)}>
        {label}
      </button>
      {copied === true && <span className="hint" role="status">Copied.</span>}
      {copied === false && <span className="hint" role="alert">The browser would not copy it: select the text above instead.</span>}
    </>
  );
}
