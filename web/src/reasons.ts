// Author: @SaadZarook
import type { Reason } from "./api/client";

export const REASON_LABELS: Record<Reason, string> = {
  incident: "Incident",
  customer_complaint: "Customer complaint",
  deploy_verification: "Deploy verification",
  audit_or_compliance: "Audit or compliance",
  proactive_check: "Proactive check",
};

/** The Reference field's example, the same in every application: it names none of them (#86). */
export const REFERENCE_PLACEHOLDER = "Jira key or case ID, such as PROJ-123";
