// Author: @SaadZarook
// Checks the folder dev/site/build.sh built, as GitHub Pages would serve it under /probeowl/: the
// landing page and every page of the guide load with no request leaving the site, no console
// error and no failed response, and the guide's search finds a page. The same check works on
// the live site: node dev/site/check_stage.mjs https://saadzarook.github.io/probeowl/
import { readdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(join(repo, "web", "package.json"));
const { chromium } = require("playwright");
const target = process.argv[2];
if (!target) throw new Error("usage: node dev/site/check_stage.mjs <built folder | site URL>");

const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".txt": "text/plain", ".xml": "application/xml" };
let base, server, pages;
if (/^https?:\/\//.test(target)) {
  base = target.endsWith("/") ? target : `${target}/`;
  // The live site's pages, from the guide's own sitemap.
  const sitemap = await (await fetch(new URL("guide/sitemap.xml", base))).text();
  pages = ["", ...[...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1].slice(base.length))];
} else {
  const root = resolve(target);
  // Served under /probeowl/, as Pages serves the repository's site, so relative paths are tested too.
  server = createServer((req, res) => {
    let path = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (!path.startsWith("/probeowl/")) return void res.writeHead(404).end();
    path = path.slice("/probeowl/".length);
    if (path === "" || path.endsWith("/")) path += "index.html";
    const file = resolve(root, path);
    try {
      if (!file.startsWith(root + sep) || !statSync(file).isFile()) throw new Error("not a file");
      res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" }).end(readFileSync(file));
    } catch {
      res.writeHead(404).end();
    }
  }).listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  base = `http://127.0.0.1:${server.address().port}/probeowl/`;
  const html = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? html(join(dir, e.name)) : e.name === "index.html" ? [relative(root, dir).split(sep).join("/")] : []);
  pages = html(root).map((p) => (p ? `${p}/` : ""));
}

const problems = [];
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const tab = await context.newPage();
let current = "";
tab.on("request", (r) => { if (!r.url().startsWith(base) && !r.url().startsWith("data:")) problems.push(`${current}: requested ${r.url()}`); });
tab.on("response", (r) => { if (r.status() >= 400) problems.push(`${current}: ${r.status()} for ${r.url()}`); });
tab.on("console", (m) => { if (m.type() === "error") problems.push(`${current}: console error: ${m.text()}`); });
tab.on("pageerror", (e) => problems.push(`${current}: ${e.message}`));
for (const page of pages) {
  current = `/${page}`;
  await tab.goto(base + page, { waitUntil: "networkidle" });
}

// The guide's search runs in the browser, from its own index: it must find a page.
current = "guide search";
await tab.goto(`${base}guide/`, { waitUntil: "networkidle" });
await tab.keyboard.press("s"); // Material's shortcut: opens the search and focuses it
await tab.keyboard.type("keychain");
try {
  await tab.locator(".md-search-result__item").first().waitFor({ timeout: 10_000 });
} catch {
  problems.push("guide search: no result for 'keychain'");
}
await browser.close();
server?.close();

if (problems.length) {
  console.error(`${base}: ${problems.length} problem(s)`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`${base}: ${pages.length} pages, no request outside the site, no console error, no failed response; search works`);
