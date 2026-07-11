# Implicit AC templates

Acceptance criteria that apply to a *class* of change automatically — appended by flow-sanity-check
and flow-review when `git diff --name-only <default_branch>...HEAD` matches a trigger glob, even if
the issue author didn't list them. Referenced by `profile.docs.ac_templates`.

<!-- SKELETON DEFAULTS — written for a deployed-service topology. flow-tailor re-derives the
     trigger groups from THIS repo's actual dangerous surfaces (e.g. a no-deploy CLI replaces the
     prod-gate group with CI/hooks/gate-script triggers). Remove this marker once tailored. -->

## Infra / CI / Deploy PRs

Apply when the diff touches any of: CI/CD workflows, container/build files (`Dockerfile`,
`docker-compose.yml`), `Makefile`, root config files, or scripts invoked by CD / the prod host.

### Default AC
- CD runs green end-to-end, including the SHA + runtime-load verify gates.
- No prod drift (the deployed checkout is clean — `git status --short` on prod returns empty).
- Post-deploy smoke: one cheap real operation succeeds on prod (an API round-trip, one job run, …).

## Dependency changes

Apply when the diff touches a dependency manifest/lockfile (`package.json`, `package-lock.json`,
`pyproject.toml`, `uv.lock`, `requirements*.txt`, `go.mod`, …).

### Default AC
- Dependency audit is clean — no high/critical CVEs (`profile.verify.security`).
- The lockfile is updated and committed (no drift between manifest and lock).
- A CVE-driven bump is folded into this PR, not split into a separate ticket.

## User-facing / public-surface changes

Apply when the diff changes a public API, CLI, config schema, env var, or user-visible behavior
(routes/handlers, public function signatures, `README`/docs, `.env.example`).

### Default AC
- Docs updated in the same PR (README / relevant guide / API doc reflects the new behavior).
- A `CHANGELOG` entry is added if the project keeps one.
- Breaking changes are called out explicitly (migration note / deprecation).

<!-- Add more classes as patterns emerge (e.g. DB migrations). -->
