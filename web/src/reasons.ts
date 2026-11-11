// Author: @SaadZarook
import type { Reason } from "./api/client";

export const REASON_LABELS: Record<Reason, string> = {
  incident: "Incident",
  customer_complaint: "Customer complaint",
  deploy_verification: "Deploy verification",
  audit_or_compliance: "Audit or compliance",
  proactive_check: "Proactive check",
};
