# Environment and Configuration

## Principles

- Store secrets in `.env`, CI secret storage, or a production secret manager; never commit them.
- Commit `.env.example` with placeholders and safe development defaults only.
- Load non-secret topic and source configuration from `config/topics.yaml` and `config/sources.yaml`.
- Validate all configuration at FastAPI startup with Pydantic Settings; fail fast on invalid values.

## Required Variables

| Variable | Development example | Notes |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test`, or `production` |
| `APP_NAME` | `Sentinel-AI` | Service name for logs/OpenAPI |
| `DATABASE_URL` | `postgresql+psycopg://sentinel:sentinel@db:5432/sentinel_ai` | Never expose in client code |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated explicit origins |
| `OPENAI_API_KEY` | `replace-me` | Secret; required only for LLM features |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Compatible provider endpoint |
| `LLM_MODEL` | provider-selected | Answer and classification model |
| `EMBEDDING_MODEL` | provider-selected | Must match indexed vectors |
| `EMBEDDING_DIMENSIONS` | provider-selected | Required for pgvector schema |
| `INGEST_API_KEY` | `local-dev-key` | Required outside development for `POST /ingest` |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` only locally |
| `REQUEST_TIMEOUT_SECONDS` | `30` | External request limit |
| `MAX_ASK_QUESTION_LENGTH` | `1000` | Request validation limit |

## Docker Compose Services

V1 runs three services: `db` (PostgreSQL + pgvector), `backend` (FastAPI), and `frontend` (React). The backend waits for the database health check before startup. Use named Docker volumes for Postgres data; never bind-mount production database storage.

## Local Setup Contract

```text
cp .env.example .env
# set OPENAI_API_KEY and provider/model values
# start with docker compose up --build
```

Actual commands may be platform-specific; the repository root README is the authoritative runnable guide once the Compose files exist.

## Environment Separation

- Development: local Docker, CORS for Vite, optional `POST /ingest` without key.
- Test: isolated database, mocked LLM provider, no real network ingestion.
- Production: explicit CORS allowlist, required ingest key, non-debug logs, secret manager, health checks, backups, and rate limiting.

Rotate any key that is accidentally committed and remove it from Git history according to the hosting provider's incident procedure.