# Web app — notes for agents

- Build each screen from its image in `docs/screens/`. Match layout, labels and
  colours; do not add features that are not in the image or the issue.
- The shell is shared by every screen: left sidebar (ProbeOwl mark, navigation:
  Inspect, Validate, Health, Probes, Knowledge base, Tables, Connectors, Runs),
  top bar with the searchable application selector and the user, and the red
  Production banner whenever the environment is Production.
- Environment colours: Production uses red #B42318 on its banner and selector;
  other environments are neutral.
- Outcome chips use one component with the eight outcomes. Filled chips describe
  data (Found, Missing, Waiting, Blocked, Ambiguous, Stale); outlined chips
  describe the system (Error, Not configured).
- Write queries in the knowledge base never get a run button.
- Call the API only through `src/api/client.ts`. `src/api/schema.d.ts` is generated: after the
  contract changes run `npm run generate:api` and commit the result (CI runs `npm run check:api`).
- Mocks are MSW handlers in `web/mocks/`, enabled with `VITE_MOCK_API=true`. Runs poll
  `GET /api/runs/{id}` until the status is no longer `running`.
- New UI is built from `src/ui/` (#105): Button, Field and FormGrid, Input, Select, Badge, Card,
  CodeBlock, Sheet, ListOrTable, and EntityGraph with its EntityOutline (#162), on Tailwind 4 and
  the tokens in `src/ui/ui.css`, the only file with colours. Screens move onto it one PR at a
  time; until then `app.css` styles them, and its rules beat Tailwind's wherever both apply.
  `e2e/screen-rules.spec.ts` marks the rules each screen still breaks: take the mark off in the PR
  that moves the screen. `/ui-gallery`, in mock builds only, shows every component, and
  `e2e/ui-rules.spec.ts` tests them there; the graph's own rules (one tab stop, arrow keys along
  the keys) are in `e2e/entity-graph.spec.ts`.
