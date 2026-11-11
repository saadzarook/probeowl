// Author: @SaadZarook
import type { CompareRequest, CompareResult, FileDifference, Range } from "../../../plugins/compare_files/web/types";
import type { PluginManifest } from "../../src/api/client";

export const compareFilesManifest: PluginManifest = {
  name: "compare_files",
  version: "1.0.0",
  kind: "tool",
  author: "@SaadZarook",
  description: "Compare two files a person uploads or pastes, on this machine, without storing them.",
  capabilities: ["local_input"],
};

function rangeOf(text: string, value: string): Range | null {
  const start = text.indexOf(value);
  return start < 0 ? null : { start, end: start + value.length };
}

function secretOf(text: string): string | undefined {
  return /api_key: (\S+)/.exec(text)?.[1];
}

function hidden(text: string): Range[] {
  const secret = secretOf(text);
  const range = secret ? rangeOf(text, secret) : null;
  return range ? [range] : [];
}

function summary(differences: FileDifference[], unchanged: number, ignored: number) {
  const count = (change: FileDifference["change"]) => differences.filter((d) => d.change === change).length;
  return {
    changed: count("changed"),
    added: count("added"),
    removed: count("removed"),
    unchanged,
    ignored,
    masked: differences.filter((d) => d.masked).length,
  };
}

function file(name: string, text: string, format: CompareResult["left"]["format"]): CompareResult["left"] {
  return { name, size: text.length, sha256: "9f2c000000000000000000000000000000000000000000000000000000a71e", format };
}

function differencesFor(body: CompareRequest): { differences: FileDifference[]; notes: string[]; withheld: boolean } {
  const left = body.left.text;
  const right = body.right.text;
  if (left.startsWith("same")) return { differences: [], notes: [], withheld: false };
  if (left.startsWith("fallback")) {
    return {
      differences: [{ path: "items[0].v", change: "changed", left: 1, right: 2, masked: false, reveal_left: null, reveal_right: null }],
      notes: ["items: matched by position; id is not a unique, unmasked key of every element"],
      withheld: false,
    };
  }
  if (left.startsWith("formula")) {
    return {
      differences: [
        { path: "a", change: "changed", left: '=HYPERLINK("x")', right: "-5", masked: false, reveal_left: null, reveal_right: null },
        { path: "b", change: "changed", left: "@cmd", right: "+1", masked: false, reveal_left: null, reveal_right: null },
      ],
      notes: [],
      withheld: false,
    };
  }
  const secretLeft = secretOf(left);
  const secretRight = secretOf(right);
  return {
    differences: [
      {
        path: "payments.api_key",
        change: "changed",
        left: "[masked]",
        right: "[masked]",
        masked: true,
        reveal_left: secretLeft ? rangeOf(left, secretLeft) : null,
        reveal_right: left.startsWith("no-reveal") ? null : secretRight ? rangeOf(right, secretRight) : null,
      },
      { path: "payments.max", change: "changed", left: 3, right: 5, masked: false, reveal_left: null, reveal_right: null },
    ],
    notes: left.startsWith("looks different") ? ["the files look like different formats (text and yaml), so they were compared as plain text"] : [],
    withheld: left.startsWith("withheld"),
  };
}

/** What the backend would say, for the shapes the tests use. Real results are tested in test_compare_files.py. */
export function compareFilesResult(body: CompareRequest): CompareResult {
  const { differences, notes, withheld } = differencesFor(body);
  return {
    left: file(body.left.name, body.left.text, "yaml"),
    right: file(body.right.name, body.right.text, "yaml"),
    summary: summary(differences, 38, 1),
    differences,
    notes: withheld ? [...notes, "the side-by-side view is hidden: a masked value could not be located in the file"] : notes,
    masked_left: withheld ? null : hidden(body.left.text),
    masked_right: withheld ? null : hidden(body.right.text),
  };
}
