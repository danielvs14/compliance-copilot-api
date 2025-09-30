# Testing Plan — Compliance Copilot Backend

## Database & Migrations
- `alembic upgrade head` against a clean Postgres container (via `docker compose up`) must succeed.
- Run `alembic downgrade base && alembic upgrade head` in CI to ensure idempotent migrations.
- Smoke check seed script: `python seed.py` twice; second run should no-op and log "Seed already applied".

## Unit Tests
1. **Regex fallback** (`api/services/regex_fallback.py`)
   - Provide sample sentences covering `daily`, `weekly`, `monthly`, `annual`, `before each use` and assert a requirement is produced with the expected frequency label.
   - Ensure duplicate sentences are deduplicated.
2. **Chunking & dedupe** (`api/services/extraction_pipeline.py`)
   - Feed synthetic 10k character string; assert it yields max 3 chunks and dedupe removes repeated titles/source refs.
3. **Translation fallback** (`api/services/extraction_pipeline.attach_translations`)
   - Mock `translate_batch_to_spanish` success/failure to ensure English fallback is applied on mismatch.
4. **Trade rules** (`api/services/trade_rules.py`)
   - Verify electrical rules set default category when missing.
5. **Confidence guardrails** (`api/routers/documents.upload_and_extract`)
   - Mock drafts with varying confidence; confirm statuses `OPEN` vs `REVIEW` when persisted (use dependency override for DB session).

## Integration Tests
1. **Upload → Extract → List**
   - Use TestClient with moto-backed S3 bucket and mocked `extract_requirement_drafts` to return deterministic drafts (≥5 items, mix of confidences).
   - Assert 400s for `.docx`, `.jpg`, files >20MB, and PDFs <200 chars.
   - Verify events inserted with `data` JSON and log line emitted.
2. **Passwordless auth flow**
   - Seed login token, hit `/auth/callback`, confirm cookie issued, `/auth/me` returns profile, `/auth/logout` revokes session.
3. **Tenancy scoping**
   - Seed two users/orgs, create requirements for each, set cookies for org A/B and assert `/requirements` only returns scoped rows.
4. **Permits & training uploads**
   - POST to `/permits/upload` and `/training/upload`, ensure files land in `s3://bucket/{org_id}/...` and presigned URLs are returned.
5. **Completion flow**
   - Call `POST /requirements/{id}/complete` and assert status transitions, `completed_at` timestamp, metrics increments, and completion event payload.
6. **Regex fallback integration**
   - Provide PDF/text fixture containing "inspect daily" phrase with LLM mocked to return empty list; ensure fallback creates requirement flagged with `origin=regex` and `status=REVIEW`.
7. **Migration/seed smoke** (CI job)
   - Spin containers, run `alembic upgrade head`, `python seed.py`, hit `/requirements` listing to ensure seeded data visible.

## Observability Checks
- Capture `document_extracted`, `permit_uploaded`, and `training_cert_uploaded` log lines via structured logger to assert org/user/request IDs present.
- Validate `OrgRequirementMetrics` rows update after creation/completion events (query DB in tests).
- Ensure auth events (`magic_link_issued`, `user_login`, `session_revoked`) emit expected metadata.

## Mocks & Fixtures
- Provide fixture for `translate_batch_to_spanish` returning deterministic Spanish text.
- Provide fixture for `extract_requirements_from_text` generating predictable drafts to avoid real OpenAI calls.
- Store PDFs under `tests/fixtures/` (already includes `electrical_sample.pdf`).

Execute with `pytest` targeting new test modules (e.g., `tests/unit/test_regex_fallback.py`, `tests/integration/test_requirements.py`).

## Running Tests Locally
- Ensure Postgres is running: `docker compose up -d`
- Activate the project virtualenv (Python 3.11 recommended) and install deps: `pip install -r requirements.txt`
- Apply migrations: `PYTHONPATH=$(pwd) alembic upgrade head`
- Export ephemeral AWS creds for moto/localstack (optional but keeps boto3 happy):
  - `export AWS_ACCESS_KEY_ID=test` `export AWS_SECRET_ACCESS_KEY=test` `export AWS_REGION=us-east-1`
- Run the suite: `pytest`
