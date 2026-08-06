# Continuous Integration

CI runs on every pull request and every push to `main`. Until a `develop` branch exists, no branch protection workflow is required; the same checks still run for all pushes.

## Required GitHub Actions Jobs

| Job | Checks | Failure condition |
| --- | --- | --- |
| `backend-quality` | Python version, dependency install, Ruff lint/format, mypy | Lint, format, or type error |
| `backend-tests` | PostgreSQL service, Alembic migration, pytest | Test or migration failure |
| `frontend-quality` | `npm ci`, ESLint, Prettier check, TypeScript check | Lint, format, or type error |
| `frontend-tests` | Vitest | Test failure |
| `frontend-build` | Vite production build | Build failure |
| `docs` | Markdown link check, Mermaid syntax/render check if tooling is available | Broken internal link or invalid diagram |
| `security` | Dependency audit and secret scan | High-severity dependency finding or credential detected |
| `compose-smoke` | Build Compose services, run health endpoint | Container or readiness failure |

## Pipeline Order

```text
format / lint / type check
          ↓
unit tests + migration validation
          ↓
frontend build + documentation checks
          ↓
security scan
          ↓
Docker Compose smoke test
```

Use dependency caches keyed by lockfiles. Pin GitHub Actions by major version initially and review upgrades regularly. Use lockfiles (`uv.lock` or equivalent; `package-lock.json`) for reproducible builds.

## Test Requirements

- Backend tests use a disposable PostgreSQL + pgvector instance and mocked LLM/embedding clients.
- No CI test may call a paid model provider or live news site.
- Contract tests validate `/api/v1/ask` citation IDs and error envelopes.
- Migration tests upgrade an empty database to head.
- Compose smoke test verifies `GET /api/v1/health` after services start.

## Security Requirements

- Run secret scanning on all commits and pull requests.
- Do not print environment variables or API request headers in CI logs.
- Use GitHub Actions secrets only for deployment jobs; normal tests use dummy keys.
- Upload test reports and coverage as artifacts, never raw production articles or database dumps.

## Release Gate

Before tagging a release, all required jobs must pass, the migration is reversible or explicitly documented, `.env.example` is current, and the README startup flow succeeds from a clean checkout.
## Current checks

- Backend: Ruff and pytest.
- Frontend: Vitest and production build.
- Deployment: `docker compose config --quiet`.
