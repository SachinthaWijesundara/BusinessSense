# BusinessSense

Invoice management and purchasing intelligence for SMEs (ModelX 2.0, Team DeepForge).

| Part | Stack | Hosted on |
|---|---|---|
| `frontend/` | Next.js owner app (five screens) | Vercel |
| `backend/` | FastAPI: invoice API, agent pipeline, business rules | Render (`render.yaml`) |
| `supabase/migrations/` | Postgres schema, pgvector, storage bucket, RLS | Supabase |

The browser talks only to the FastAPI backend; OpenAI and Supabase service keys stay on Render.
See [backend/README.md](backend/README.md) for the pipeline, endpoints and tests.

## Local setup

1. Copy `.env.example` to `.env` at the repo root and fill in the keys.
2. Database: apply `supabase/migrations/*.sql` in order (Supabase CLI `supabase db push`, or the SQL
   editor). Skip the RLS migration until the frontend goes through the API (see its header).
3. Backend (port 8765 matches the frontend's default `NEXT_PUBLIC_EXTRACT_API_URL`):

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\uvicorn main:app --reload --port 8765
```

4. Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3001. API docs: http://localhost:8765/docs
