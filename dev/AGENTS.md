# Local stack — notes for agents

- `dev/docker-compose.yml` must start with one command and become healthy
  without manual steps.
- Every probed database gets two logins: `probeowl_ro` (read-only, used by
  ProbeOwl) and `seed_rw` (used only by seed scripts and lock tests).
- Seed data is the shared test data for both agents. Keep the planted cases in
  `dev/seed/PLANTED.md` up to date; tests and screens rely on those exact IDs.
- No real customer data, real names or real emails. Use clearly fake values.
