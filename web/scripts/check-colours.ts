// Author: @SaadZarook
// Rule 10 (#105): a colour is written only in web/src/ui/ui.css, as a token; everything else reads
// the tokens, so both themes reach every screen. Comments are skipped: "#110" there is an issue.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const WEB = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const ROOTS = [join(WEB, "src"), join(WEB, "mocks", "gallery"), join(WEB, "..", "plugins")];
const TOKENS = join(WEB, "src", "ui", "ui.css");
const LITERAL = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|\brgba?\(|\bhsla?\(/g;

function files(folder: string): string[] {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (name === "node_modules" || name === "dist") return [];
    if (statSync(path).isDirectory()) return files(path);
    return /\.(css|tsx?)$/.test(name) && !name.endsWith(".d.ts") ? [path] : [];
  });
}

/** The text with its comments blanked, line breaks kept so line numbers still match. */
function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " ")).replace(/(^|[^:"'])\/\/[^\n]*/g, "$1");
}

const found: string[] = [];
for (const path of ROOTS.flatMap(files)) {
  if (path === TOKENS) continue;
  withoutComments(readFileSync(path, "utf8"))
    .split("\n")
    .forEach((line, i) => {
      for (const match of line.matchAll(LITERAL)) found.push(`${relative(WEB, path)}:${i + 1}: ${match[0]}`);
    });
}
if (found.length > 0) {
  console.error(`A colour outside web/src/ui/ui.css (rule 10): add a token there and read it.\n${found.join("\n")}`);
  process.exit(1);
}
console.log("No colour outside web/src/ui/ui.css.");
