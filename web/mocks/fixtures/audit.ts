// Author: @SaadZarook
import type { AuditEvent } from "../../src/api/client";

const person = { kind: "person" as const, name: "s.khan" };
const agent = { kind: "agent" as const, name: "s.khan via MCP", on_behalf_of: "s.khan" };

/** 53 events, newest first, as GET /api/audit lists them: masked as owl serve masks them. */
export const auditEvents: AuditEvent[] = [
  { id: "ev-53", occurred_at: "2026-11-15T10:53:00Z", action: "mcp_tool_called", actor: agent, application: "card_access", environment: "prod", run_id: null, details: { tool: "check_draft", kind: "probe", application: "card_access", environment: "prod", ok: false, reason: "production_refused" } },
  { id: "ev-52", occurred_at: "2026-11-15T10:52:00Z", action: "run_started", actor: person, application: "card_access", environment: "uat", run_id: "run_card", details: { probe: "visitor_cards", entity: "visitor", identifiers: { email: "[masked]" }, reason: "customer_complaint", reference: "SUP-20931" } },
  { id: "ev-51", occurred_at: "2026-11-15T10:51:00Z", action: "connection_tested", actor: person, application: "wallet", environment: "uat", run_id: null, details: { application: "wallet", connector: "wallet_db", environment: "uat", reachable: false, reason: "authentication failed" } },
  ...Array.from({ length: 50 }, (_, i): AuditEvent => ({
    id: `ev-${String(50 - i).padStart(2, "0")}`,
    occurred_at: `2026-11-15T09:${String(50 - i).padStart(2, "0")}:00Z`,
    action: "secret_saved",
    actor: person,
    application: "wallet",
    environment: "uat",
    run_id: null,
    details: { connector: "wallet_db", replaced: false },
  })),
];
