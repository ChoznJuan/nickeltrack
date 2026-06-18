# NickelTrack

Low-nickel food tracking — search, scan, log daily intake.

Single-tenant Flask app backed by Postgres (pgvector-compatible). State held client-side in `localStorage` for v1.

## Stack

- **Backend:** Flask + gunicorn
- **Database:** PostgreSQL (uses `nickeltrack.foods` and `nickeltrack.servings` tables)
- **Data source:** USDA FoodData Central (US), ingested via the schema in `projects/nickeltrack/scripts/schema.sql`

## Routes

- `GET  /` — index page (search + meal builder UI)
- `GET  /api/search?q=...&category=high|medium|low` — food search
- `GET  /api/food/<id>` — food detail
- `GET  /api/config` — app config (daily nickel targets)
- `POST /api/totals` — `{items: [{food_id, servings}, ...]}` → daily totals

## Local dev

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export MEMORY_DB_URL="postgresql://USER:PASS@HOST:5432/DBNAME"
python app.py
# → http://localhost:5000
```

## Deployment

CI/CD via GitHub Actions with a **self-hosted runner** living in the production LXC container.

- **Repo:** https://github.com/ChoznJuan/nickeltrack
- **Runner host:** CT 110 on Mace (Proxmox)
- **Workflow:** `.github/workflows/deploy.yml` — runs on push to `main`, deploys to the running container, restarts the `nickeltrack` systemd service.

Push to `main` → auto-deploys.

## Environment

- `MEMORY_DB_URL` — Postgres DSN (e.g. `postgresql://user:pass@host:5432/db`)
- `MEMORY_DB_TYPE` — `pgvector` (default for this stack)

## Schema

DB schema and seed data live in `projects/nickeltrack/scripts/schema.sql` and `ingest_mislankar.py` (kept in the workspace, not the repo — they're one-time bootstrap, not part of the app's lifecycle).
