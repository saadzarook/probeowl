// Author: @SaadZarook
import type { ApplicationSummary, EnvironmentSummary } from "../../src/api/client";

const prod: EnvironmentSummary = { name: "prod", title: "Production", production: true };
const uat: EnvironmentSummary = { name: "uat", title: "UAT", production: false };
const staging: EnvironmentSummary = { name: "staging", title: "Staging", production: false };
const dev: EnvironmentSummary = { name: "dev", title: "Dev", production: false };

/** Applications that only fill the selector; they have no folder in examples/, so check:mocks skips them. */
export const UI_ONLY_APPLICATIONS = ["onboarding", "orders", "payouts"];

export const applications: ApplicationSummary[] = [
  { name: "wallet", title: "Wallet", owner: "payments-team", environments: [prod, uat] },
  { name: "card_access", title: "Card access", owner: "access-team", environments: [prod, uat] },
  { name: "onboarding", title: "Onboarding", owner: "onboarding-team", environments: [prod, uat, staging, dev] },
  { name: "orders", title: "Orders", owner: "commerce-team", environments: [prod, uat] },
  { name: "payouts", title: "Payouts", owner: "payments-team", environments: [prod, uat] },
];
