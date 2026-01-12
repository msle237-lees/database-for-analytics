# database-for-analytics
A SQL setup for my personal homelab for analytics learning.

## Run (host)
1) Copy env:
   - `cp .env.example .env`
2) Install:
   - `pip install -e .`
3) Start API:
   - `uvicorn app.main:app --reload --app-dir src`

## Run SQL Server (later)
- `docker compose up -d`

## Docs
- http://127.0.0.1:8000/docs

## Notes
- Identifier validation is enforced (table/column/schema names).
- Values are parameterized.
- Column types are validated against an allowlist for safety.
