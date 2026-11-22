// Author: @SaadZarook
import { useState } from "react";
import { Card, EntityGraph, EntityOutline, type GraphKey, type GraphLocation } from "../../src/ui";

// The entity graph and its Outline on the gallery, for e2e/ui-rules.spec.ts (#162). Two entities
// from examples/: Card access's visitor, with a fan-out whose controller is Missing for one card,
// and Wallet's top-up, with a Missing location and a fix. The outcomes are fixtures: the graph
// screen (#163) gets them from a draft test run.

interface Example {
  slug: string;
  title: string;
  locations: GraphLocation[];
  keys: GraphKey[];
}

const FOUND = { tone: "found", label: "Found" } as const;
const MISSING = { tone: "missing", label: "Missing" } as const;

const VISITOR: Example = {
  slug: "visitor",
  title: "Visitor",
  locations: [
    { id: "profile", title: "Visitor profile", connector: "access_db", source: "visitor_profile", cardinality: "one", root: true, outcome: FOUND, detail: "1 row" },
    { id: "cards", title: "Cards", connector: "access_db", source: "cards", cardinality: "many", outcome: FOUND, detail: "2 rows" },
    {
      id: "cards.controller",
      title: "Door controller",
      connector: "door_controller_api",
      source: "GET /cards/{card_id}",
      cardinality: "one",
      fanOut: true,
      outcome: MISSING,
      detail: "1 of 2 cards: C104577 not on the controller",
    },
  ],
  keys: [
    { from: "profile", to: "cards", field: "visitor_profile_id" },
    { from: "cards", to: "cards.controller", field: "card_id" },
  ],
};

const TOPUP: Example = {
  slug: "topup",
  title: "Top-up",
  locations: [
    { id: "record", title: "Top-up record", connector: "wallet_db", source: "topups", cardinality: "one", root: true, outcome: FOUND, detail: "1 row" },
    { id: "payment", title: "Provider payment", connector: "checkout_api", source: "GET /payments/{provider_payment_id}", cardinality: "one", outcome: FOUND, detail: "200" },
    { id: "webhook", title: "Webhook deliveries", connector: "wallet_db", source: "provider_webhooks", cardinality: "many", outcome: MISSING, detail: "no row" },
    {
      id: "ledger",
      title: "Ledger entries",
      connector: "ledger_db",
      source: "ledger_entries",
      cardinality: "many",
      outcome: FOUND,
      detail: "2 rows",
      fix: "Confirm label: Transaction",
    },
  ],
  keys: [
    { from: "record", to: "payment", field: "provider_payment_id" },
    { from: "record", to: "webhook", field: "provider_payment_id" },
    { from: "record", to: "ledger", field: "ledger_txn_id" },
  ],
};

function GraphExample({ example }: { example: Example }) {
  const [selected, setSelected] = useState(example.locations[0]?.id ?? "");
  const shown = example.locations.find((l) => l.id === selected);
  return (
    <Card aria-labelledby={`graph-${example.slug}`}>
      <h2 id={`graph-${example.slug}`} className="m-0 mb-4 text-lg font-semibold">
        Entity graph: {example.title}
      </h2>
      <p className="m-0 mb-4 text-sm" data-selected>
        Selected: {shown?.title ?? "none"}
      </p>
      <div className="grid gap-6">
        <EntityGraph label={`Entity graph of ${example.title}`} locations={example.locations} keys={example.keys} selected={selected} onSelect={setSelected} />
        <section aria-labelledby={`outline-${example.slug}`}>
          <h3 id={`outline-${example.slug}`} className="m-0 mb-2 text-base font-semibold">
            Outline of {example.title}
          </h3>
          <EntityOutline label={`Outline of ${example.title}`} locations={example.locations} keys={example.keys} selected={selected} onSelect={setSelected} />
        </section>
      </div>
    </Card>
  );
}

/** Both examples, each as the graph and as its Outline, sharing one selection. */
export function GraphGallery() {
  return (
    <>
      <GraphExample example={VISITOR} />
      <GraphExample example={TOPUP} />
    </>
  );
}
