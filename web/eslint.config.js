// Author: @SaadZarook
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// A Tailwind class off the spacing scale, or with an arbitrary value, in a string that is a class
// name: a className, or an argument of cn() or cva().
const OFF_SCALE = String.raw`/(^|\s)([a-z0-9-]+:)*-?(p|px|py|pt|pr|pb|pl|ps|pe|m|mx|my|mt|mr|mb|ml|ms|me|gap|gap-x|gap-y|space-x|space-y)-(0\.5|1\.5|2\.5|3\.5|5|7|9|10|11|1[3-9]|[2-9][0-9])(\s|$)/`;
const ARBITRARY = String.raw`/(^|\s)([a-z0-9-]+:)*-?[a-z][a-z0-9-]*-\[/`;
// Each string once: a className's own string, or any string inside cn() or cva().
const CLASS_NAME = "JSXAttribute[name.name='className']";
const CLASS_CALL = "CallExpression[callee.name=/^(cn|cva)$/]";
const classRules = (pattern, message) =>
  [
    `${CLASS_NAME} > Literal[value=${pattern}]`,
    `${CLASS_NAME} > JSXExpressionContainer > Literal[value=${pattern}]`,
    `${CLASS_NAME} > JSXExpressionContainer > TemplateLiteral > TemplateElement[value.raw=${pattern}]`,
    `${CLASS_CALL} Literal[value=${pattern}]`,
    `${CLASS_CALL} TemplateElement[value.raw=${pattern}]`,
  ].map((selector) => ({ selector, message }));

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
    // The UI foundation's spacing scale (#105, rule 1): 4, 8, 12, 16, 24, 32 and 48 px only, so
    // padding, margin and gaps of 0, 1, 2, 3, 4, 6, 8 and 12. In class names, everywhere.
    files: ["src/**/*.tsx", "mocks/**/*.tsx", "plugins/**/*.tsx"],
    rules: {
      "no-restricted-syntax": ["error", ...classRules(OFF_SCALE, "Off the spacing scale: use 0, 1, 2, 3, 4, 6, 8 or 12 (4 to 48 px).")],
    },
  },
  {
    // Arbitrary values ("gap-[…]") only inside web/src/ui, where the components are made.
    files: ["src/**/*.tsx", "mocks/**/*.tsx", "plugins/**/*.tsx"],
    ignores: ["src/ui/**"],
    rules: {
      "no-restricted-syntax": [
        "error",
        ...classRules(OFF_SCALE, "Off the spacing scale: use 0, 1, 2, 3, 4, 6, 8 or 12 (4 to 48 px)."),
        ...classRules(ARBITRARY, "An arbitrary value: add it to a component in web/src/ui instead."),
      ],
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
