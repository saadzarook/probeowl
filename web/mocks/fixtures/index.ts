// Author: @SaadZarook
import type { Run } from "../../src/api/client";
import { topUpEntity, walletProbes } from "./entities";
import { cardAccessCatalog, schemaSnapshots, walletCatalog } from "./catalog";
import { cardAccessReview, walletReviewTables } from "./review";
import { cardAccessConnectors, walletConnectors } from "./connectors";
import { draftRun } from "./probeChecks";
import { topUpNotCredited } from "./runbooks";
import { failedValidateRun, runningValidateRun, skippedValidateRun, stoppedValidateRun, lateValidateRun } from "./runs";
import {
  ambiguousEmailRun,
  cardAccessProbes,
  inProduction,
  inactiveVisitorRun,
  plantedCardRun,
  skippedCardRun,
  stoppedCardRun,
  runningCardRun,
  unknownVisitorRun,
  visitorEntity,
} from "./cardAccess";

export { applications, UI_ONLY_APPLICATIONS } from "./applications";
export { currentUser, type User } from "./user";
export { topUpEntity, walletProbes } from "./entities";
export { topUpDifferences, afterARestart, allSame, allNotCompared, someNotCompared } from "./healthyComparison";
export { flaggedRunbook, outboxResult, stepsInRun, topUpNotCredited } from "./runbooks";
export { walletConnectors, cardAccessConnectors, payoutsConnectors } from "./connectors";
export { walletCatalog, cardAccessCatalog, schemaSnapshots, truncatedAccessSnapshot, UNIMPORTED } from "./catalog";
export { walletSuggestions, walletReviewTables, cardAccessReview } from "./review";
export { draftRun, passingReport, piiReport, timeFilterReport, unreadableReport, TIME_FILTER_MESSAGE } from "./probeChecks";
export { runningValidateRun, failedValidateRun, skippedValidateRun, stoppedValidateRun, lateValidateRun, walletRunPages } from "./runs";
export {
  visitorEntity,
  cardAccessProbes,
  runningCardRun,
  plantedCardRun,
  inactiveVisitorRun,
  ambiguousEmailRun,
  unknownVisitorRun,
  inProduction,
  skippedCardRun,
  stoppedCardRun,
  replicaLagWarning,
} from "./cardAccess";

/** Every mocked entity and probe list, with the fixture file it lives in; `npm run check:mocks` compares them with examples/. */
export const mockedEntities = [
  { file: "mocks/fixtures/entities.ts", application: "wallet", entity: topUpEntity },
  { file: "mocks/fixtures/cardAccess.ts", application: "card_access", entity: visitorEntity },
];
export const mockedProbes = [
  { file: "mocks/fixtures/entities.ts", application: "wallet", probes: walletProbes },
  { file: "mocks/fixtures/cardAccess.ts", application: "card_access", probes: cardAccessProbes },
];

/** Every mocked catalog and schema snapshot, with the fixture file it lives in; `npm run check:mocks` compares them with examples/. */
export const mockedCatalogs = [
  { file: "mocks/fixtures/catalog.ts", application: "wallet", catalog: walletCatalog },
  { file: "mocks/fixtures/catalog.ts", application: "card_access", catalog: cardAccessCatalog },
];
/** Every mocked review table, with the application it belongs to; `npm run check:mocks` compares them with examples/. */
export const mockedReviews = [
  { file: "mocks/fixtures/review.ts", application: "wallet", tables: walletReviewTables() },
  { file: "mocks/fixtures/review.ts", application: "card_access", tables: cardAccessReview.tables },
];
export const mockedSnapshots = schemaSnapshots.map((snapshot) => ({ file: "mocks/fixtures/catalog.ts", snapshot }));

/** Every run fixture, built once per shape; add a new run fixture here so `npm run check:mocks` covers it. */
const cardRuns = ["visitor_profile_id:VP00012931", "card_id:C204518", "email:a.rahman@example.com"].flatMap((pair) => {
  const [identifier = "", value = ""] = pair.split(":");
  const card = (name: string, run: Run) => ({ file: "mocks/fixtures/cardAccess.ts", name: `${name}(${identifier})`, run });
  return [
    card("runningCardRun", runningCardRun("run_c", identifier, value)),
    card("plantedCardRun", plantedCardRun("run_d", identifier, value)),
    card("inactiveVisitorRun", inactiveVisitorRun("run_e", identifier, value)),
    card("unknownVisitorRun", unknownVisitorRun("run_g", identifier, value)),
    card("skippedCardRun", skippedCardRun("run_h", identifier, value)),
    card("stoppedCardRun", stoppedCardRun("run_k", identifier, value)),
    card("inProduction(plantedCardRun)", inProduction(plantedCardRun("run_i", identifier, value))),
  ];
});

/** Every mocked runbook that mirrors a real one; `npm run check:mocks` compares them with examples/. */
export const mockedRunbooks = [{ file: "mocks/fixtures/runbooks.ts", application: "wallet", runbook: topUpNotCredited }];

export const mockedRuns = [
  { file: "mocks/fixtures/runs.ts", name: "runningValidateRun", run: runningValidateRun("run_a") },
  { file: "mocks/fixtures/runs.ts", name: "failedValidateRun", run: failedValidateRun("run_b") },
  { file: "mocks/fixtures/runs.ts", name: "skippedValidateRun", run: skippedValidateRun("run_j") },
  { file: "mocks/fixtures/runs.ts", name: "stoppedValidateRun", run: stoppedValidateRun("run_l") },
  { file: "mocks/fixtures/runs.ts", name: "lateValidateRun", run: lateValidateRun("run_late") },
  { file: "mocks/fixtures/cardAccess.ts", name: "ambiguousEmailRun", run: ambiguousEmailRun("run_f", "email") },
  { file: "mocks/fixtures/probeChecks.ts", name: "draftRun", run: draftRun("run_k") },
  ...cardRuns,
];

/** Every mocked connector status list that mirrors a real application; `npm run check:mocks` compares them with examples/. */
export const mockedConnectors = [
  { file: "mocks/fixtures/connectors.ts", application: "wallet", environment: "uat", statuses: walletConnectors("uat", false) },
  { file: "mocks/fixtures/connectors.ts", application: "wallet", environment: "prod", statuses: walletConnectors("prod", true) },
  { file: "mocks/fixtures/connectors.ts", application: "card_access", environment: "uat", statuses: cardAccessConnectors("uat", false) },
  { file: "mocks/fixtures/connectors.ts", application: "card_access", environment: "prod", statuses: cardAccessConnectors("prod", true) },
];
