// Author: @SaadZarook
// The landing page's own checks, run by dev/site/check.sh: no request leaves the site, the CSP is
// there and allows the page's one inline script by its hash, and the page fits at 390 px in both
// themes. Uses the Playwright that web/ installs (npm ci in web/, then npx playwright install).
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(join(repo, "web", "package.json"));
const { chromium } = require("playwright");
const page = process.argv[2] ? resolve(process.argv[2]) : join(repo, "site", "index.html");
const html = readFileSync(page, "utf-8");
const problems = [];

// 1. No external URL in src, href or url(), except outbound <a> links.
for (const tag of html.matchAll(/<([a-z]+)\b[^>]*>/gi)) {
  const [text, name] = tag;
  if (name.toLowerCase() === "a") continue;
  for (const attribute of text.matchAll(/\b(src|href|srcset|poster|data)\s*=\s*["']\s*((?:https?:)?\/\/[^"']*)/gi)) {
    problems.push(`<${name}> loads ${attribute[2]}`);
  }
}
for (const found of html.matchAll(/url\(\s*['"]?\s*((?:https?:)?\/\/[^)'"]*)/gi)) problems.push(`url() loads ${found[1]}`);
for (const found of html.matchAll(/@import\s+[^;]*/gi)) problems.push(`@import: ${found[0]}`);

// 2. The CSP is there, allows nothing by default, and pins each inline script by its hash.
const csp = html.match(/<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"/i)?.[1];
if (!csp) problems.push("no Content-Security-Policy meta tag");
else {
  if (!csp.includes("default-src 'none'")) problems.push("the CSP does not start from default-src 'none'");
  if (/https?:|\*/.test(csp)) problems.push(`the CSP allows another origin: ${csp}`);
  for (const script of html.matchAll(/<script>([\s\S]*?)<\/script>/gi)) {
    const digest = createHash("sha256").update(script[1], "utf-8").digest("base64");
    if (!csp.includes(`'sha256-${digest}'`)) problems.push(`an inline script's hash is not in the CSP: sha256-${digest}`);
  }
}

// 3. Every local file the page names exists (fonts), with the fonts' licences beside them.
for (const found of html.matchAll(/url\(\s*['"]?(fonts\/[^)'"]+)/g)) {
  if (!existsSync(join(repo, "site", found[1]))) problems.push(`missing ${found[1]}`);
}
for (const family of ["ibm-plex-sans", "ibm-plex-mono", "ibm-plex-sans-condensed"]) {
  if (!existsSync(join(repo, "site", "fonts", `OFL-${family}.txt`))) problems.push(`missing fonts/OFL-${family}.txt`);
}

// 4. In a browser: nothing requested off the site, no CSP violation, no sideways scroll at 390 px.
const browser = await chromium.launch();
for (const width of [390, 1440]) {
  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
    const tab = await context.newPage();
    tab.on("request", (r) => {
      if (!r.url().startsWith("file:") && !r.url().startsWith("data:")) problems.push(`${width}/${scheme}: requested ${r.url()}`);
    });
    tab.on("console", (m) => {
      if (/Content Security Policy|Refused to/i.test(m.text())) problems.push(`${width}/${scheme}: ${m.text()}`);
    });
    await tab.goto(pathToFileURL(page).href, { waitUntil: "networkidle" });
    await tab.evaluate(() => document.fonts.ready);
    const loaded = await tab.evaluate(() => [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family));
    if (!loaded.some((f) => f.includes("IBM Plex Sans"))) problems.push(`${width}/${scheme}: IBM Plex Sans did not load from fonts/`);
    const over = await tab.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (over > 0) problems.push(`${width}/${scheme}: the page scrolls sideways by ${over} px`);
    await context.close();
  }
}
await browser.close();

if (problems.length > 0) {
  console.error(`site/index.html: ${problems.length} problem(s)`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log("site/index.html: no external requests, CSP present and matching, fits at 390 px in both themes");
