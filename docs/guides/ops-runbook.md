# Ops Runbook

Grep-first failure-mode catalog — the index into hard-won operational muscle memory. When prod
breaks, grep here first. `flow-investigator` and `flow-server-audit` read this. Every reliability
ticket should add or extend an entry.

Each entry follows the same shape:

## <Failure mode — a symptom you'd actually search for>

- **Symptom:** what you observe (the alert, the error, the user report).
- **Diagnosis:** the exact commands to confirm the cause (debug *state*, not logs — query the
  system's own status first).
- **Response:** the steps to remediate, safest first.
- **Prevention:** the guard (CI check / ship hard-gate / config) that now stops it recurring — or
  "accepted risk: <reason>".

---

<!-- Add real entries as incidents happen. Starter examples of the kind of thing that belongs here: -->

## Deploy reports success but prod runs stale code
- **Symptom:** CD green, but behavior doesn't match the merged commit.
- **Diagnosis:** compare `profile.deploy.deployed_sha` to the merge commit; check for drift on the host.
- **Response:** investigate the drift, re-trigger the deploy.
- **Prevention:** CD hard gate 1 (prod SHA == merge) — see `.github/workflows/cd.yml`.

## Service is "up" but not serving
- **Symptom:** container/process running, health check failing or requests hanging.
- **Diagnosis:** `profile.deploy.health`; `profile.deploy.runtime_id` (did the process load the code?).
- **Response:** restart per your platform; check for a load-time error.
- **Prevention:** CD hard gate 2 (runtime-load) + a post-deploy smoke check.
