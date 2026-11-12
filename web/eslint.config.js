// Author: @SaadZarook
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["src/api/schema.d.ts", "public/mockServiceWorker.js", "dist", "dist-e2e", "node_modules", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
    },
  },
  {
    // Run from the repository root for this folder: see the lint script in package.json.
    files: ["plugins/**/*.{ts,tsx}"],
    rules: {
      // A plugin's screens may import the plugin web API, and nothing else of the app.
      "no-restricted-imports": [
        "error",
        { patterns: [{ group: ["**/web/src/**", "**/src/**", "@plugins/*"], message: "Import from @probeowl/plugin-web." }] },
      ],
    },
  },
);
