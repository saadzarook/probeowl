// Author: @SaadZarook
import { useState } from "react";
import {
  Badge,
  Button,
  buttonClass,
  Card,
  CodeBlock,
  Field,
  FormGrid,
  Input,
  ListOrTable,
  Select,
  Sheet,
  type BadgeTone,
  type Column,
} from "../../src/ui";
import { GraphGallery } from "./GraphGallery";

// Every component of web/src/ui on one page, for e2e/ui-rules.spec.ts (#130). Mock builds only:
// main.tsx renders it at /ui-gallery. ?theme=light, dark or system sets the page's theme.

const OUTCOMES: [BadgeTone, string][] = [
  ["found", "Found"],
  ["missing", "Missing"],
  ["waiting", "Waiting"],
  ["blocked", "Blocked"],
  ["ambiguous", "Ambiguous"],
  ["stale", "Stale"],
  ["error", "Error"],
  ["not-configured", "Not configured"],
  ["production", "Production"],
];

// A 200-character identifier: the command must wrap inside its block, and Copy stay clear of it.
const LONG_ID = `tp_${"9f2c7a".repeat(32)}9f2c7`;

type Binding = { connector: string; role: string; outcome: BadgeTone; label: string };
const BINDINGS: Binding[] = [
  { connector: "wallet_db", role: "Read replica", outcome: "found", label: "Found" },
  { connector: "ledger_db", role: "Readable secondary", outcome: "missing", label: "Missing" },
  { connector: "card_access_db", role: "Active Data Guard standby", outcome: "not-configured", label: "Not configured" },
];
const COLUMNS: Column<Binding>[] = [
  { header: "Connector", cell: (row) => <code className="font-mono text-xs">{row.connector}</code> },
  { header: "Role", cell: (row) => row.role },
  { header: "Status", cell: (row) => <Badge tone={row.outcome}>{row.label}</Badge> },
];

function themeFromQuery(): void {
  const theme = new URLSearchParams(window.location.search).get("theme");
  if (theme === "light" || theme === "dark" || theme === "system") document.documentElement.dataset["theme"] = theme;
}

/** The gallery of the UI components, light, dark or following the system. */
export function UiGallery() {
  themeFromQuery();
  const [showError, setShowError] = useState(false);
  const [menu, setMenu] = useState(false);
  return (
    <main className="flex min-h-screen flex-col gap-8 bg-background p-4 font-sans text-foreground md:p-8">
      <h1 className="m-0 text-2xl font-semibold">UI components</h1>

      <Card aria-labelledby="form-title">
        <h2 id="form-title" className="m-0 mb-4 text-lg font-semibold">
          Form
        </h2>
        <form aria-label="Validate" onSubmit={(event) => event.preventDefault()}>
          <FormGrid>
            <Field id="environment" label="Environment">
              <Select tone="production" defaultValue="production">
                <option value="production">Production</option>
                <option value="uat">UAT</option>
              </Select>
            </Field>
            <Field id="entity" label="Entity">
              <Select defaultValue="topup">
                <option value="topup">Top-up</option>
                <option value="visitor">Visitor</option>
              </Select>
            </Field>
            <Field id="any-id" label="Any ID" help="Accepts any identifier of a top-up" error={showError ? "Enter an identifier" : undefined}>
              <Input placeholder="a topup_id or another identifier" />
            </Field>
            {/* Help that wraps to two lines at four columns: its row must not push the other fields' controls down. */}
            <Field id="reference" label="Reference" help="The ticket or case this run is for, recorded with the run in the audit">
              <Input defaultValue="WALLET-482" />
            </Field>
          </FormGrid>
          <div className="flex flex-wrap gap-2">
            <Button type="submit">Validate</Button>
            <Button variant="outline" onClick={() => setShowError((shown) => !shown)}>
              {showError ? "Hide the error" : "Show an error"}
            </Button>
          </div>
        </form>
      </Card>

      <Card aria-labelledby="buttons-title" id="buttons">
        <h2 id="buttons-title" className="m-0 mb-4 text-lg font-semibold">
          Buttons
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button>Save</Button>
          <Button variant="outline">Preview</Button>
          <Button variant="ghost">Cancel</Button>
          <Button variant="outline" size="sm">
            Undo
          </Button>
          <Button disabled disabledReason="Fill in every field first">
            Save connection
          </Button>
          <a href="#buttons" className={buttonClass({ variant: "outline" })}>
            Add connection
          </a>
        </div>
      </Card>

      <Card aria-labelledby="chips-title">
        <h2 id="chips-title" className="m-0 mb-4 text-lg font-semibold">
          Status chips
        </h2>
        <ul aria-label="Status chips" className="m-0 flex list-none flex-wrap gap-2 p-0">
          {OUTCOMES.map(([tone, label]) => (
            <li key={tone}>
              <Badge tone={tone}>{label}</Badge>
            </li>
          ))}
        </ul>
      </Card>

      <Card aria-labelledby="code-title">
        <h2 id="code-title" className="m-0 mb-4 text-lg font-semibold">
          Code
        </h2>
        <CodeBlock code={`owl validate topup_completed ${LONG_ID} --env production`} copyLabel="Copy command" />
      </Card>

      <Card aria-labelledby="bindings-title">
        <h2 id="bindings-title" className="m-0 mb-4 text-lg font-semibold">
          Bindings
        </h2>
        <ListOrTable label="Bindings" rows={BINDINGS} columns={COLUMNS} rowKey={(row) => row.connector} empty="No bindings yet." highlight={(row) => (row.outcome === "missing" ? "missing" : undefined)} />
        <ListOrTable label="No bindings" rows={[]} columns={COLUMNS} rowKey={(row) => row.connector} empty="No bindings yet." />
      </Card>

      <Card aria-labelledby="sheet-title">
        <h2 id="sheet-title" className="m-0 mb-4 text-lg font-semibold">
          Sheet
        </h2>
        <Sheet trigger={<Button variant="outline">Menu</Button>} open={menu} onOpenChange={setMenu} title="ProbeOwl" description="The screens of ProbeOwl">
          <nav aria-label="Screens" className="flex flex-col gap-1">
            {["Inspect", "Validate", "Connectors"].map((screen) => (
              <a key={screen} href="#sheet-title" className="rounded-md px-3 py-2 text-sm text-sidebar-foreground no-underline hover:bg-sidebar-active">
                {screen}
              </a>
            ))}
          </nav>
        </Sheet>
      </Card>

      <GraphGallery />
    </main>
  );
}
