# BusinessSense

Invoice management and purchasing intelligence for SMEs (ModelX 2.0, Team DeepForge).
The repository follows the proposal's architecture: three parts, each with one job.

| Folder | What it is | Runs on |
|---|---|---|
| [`frontend/`](frontend/README.md) | Next.js owner app: the five screens. It only sends HTTP GET/POST requests to the backend; it holds no keys and does no business logic. | Vercel |
| [`backend/`](backend/README.md) | FastAPI: invoice API, the OpenAI Agents SDK pipeline (InvoiceVisionAgent, SkuMatchAgent), the business rules, and all reads/writes to Supabase. | Render (`render.yaml`) |
| `database/` | The Supabase PostgreSQL schema, as three SQL files applied in order. | Supabase |

```
Browser (frontend) --HTTPS--> Backend (FastAPI) --SQL--> Supabase Postgres + Storage
                                     |
                                     +--HTTPS--> OpenAI (gpt-4o vision, text-embedding-3-small)
```

## Set up locally

1. **Database**: in the Supabase SQL editor run `database/01_schema.sql`, then `database/02_storage.sql`.
   Run `database/03_row_level_security.sql` once nothing else writes to Supabase with the public anon key.
2. **Backend** (port 8765):

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\uvicorn main:app --reload --port 8765
```

   Fill `backend/.env` first (`DATABASE_URL` is required, plus `OPENAI_API_KEY`).
3. **Frontend** (port 3001):

```bash
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

Open http://localhost:3001. The API's own docs are at http://localhost:8765/docs.

## Deploy

- **Supabase**: apply the SQL files as above.
- **Render**: New > Blueprint > this repository (`render.yaml`); enter `OPENAI_API_KEY`, `DATABASE_URL`
  (Supabase "Transaction pooler" string), `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `ALLOWED_ORIGINS`
  (the Vercel address).
- **Vercel**: root directory `frontend`; set `NEXT_PUBLIC_API_URL` to the Render address.

## Privacy

This repository is public. Never commit the participating shop's real name, supplier names, invoices or
photos; all test data is fictional.
