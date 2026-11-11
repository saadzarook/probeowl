// Author: @SaadZarook
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Bound to loopback only: ProbeOwl never listens on other interfaces by default.
// /api and the guide (/guide) go to `owl serve`; with VITE_MOCK_API=true the mocks answer /api first.
// changeOrigin makes Host name owl serve; Origin is left alone, owl serve checks it against its web origins.
// PROBEOWL_API_URL points at an owl serve on another port, such as a second one for the live tests.
const owlServe = { target: process.env["PROBEOWL_API_URL"] ?? "http://127.0.0.1:7878", changeOrigin: true };
const proxy = { "/api": owlServe, "/guide": owlServe };

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

// The package's version, where owl --version reads it from (the installed package's metadata).
const pyproject = readFileSync(here("../backend/pyproject.toml"), "utf8");
const version = /^version = "([^"]+)"$/m.exec(pyproject)?.[1];
if (version === undefined) throw new Error("version not found in backend/pyproject.toml");

export default defineConfig({
  plugins: [react()],
  define: { __PROBEOWL_VERSION__: JSON.stringify(version) },
  resolve: {
    alias: {
      "@plugins": here("../plugins"),
      "@probeowl/plugin-web": here("./src/pluginApi.ts"),
    },
    // A plugin's screens live outside web/, and must share the app's one copy of each of these.
    dedupe: ["react", "react-dom", "@tanstack/react-query"],
  },
  server: { host: "127.0.0.1", port: 5173, proxy, fs: { allow: [here(".."), here(".")] } },
  preview: { host: "127.0.0.1", port: 4173, proxy },
});
