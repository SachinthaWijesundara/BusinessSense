# BusinessSense

Owner UI + invoice extract API. Layout matches this repo:

- `frontend/` — Next.js Invoice Manager (port 3001)
- `backend/` — FastAPI CLAHE + vision extract (port 8765)

## Setup

1. Copy `.env.example` → `.env` at the **repo root** (`BusinessSense/.env`) and fill keys.  
   Next.js needs that file visible as `frontend/.env` — create a hard link once (Windows):  
   `cmd /c mklink /H frontend\.env .env`  
   (one physical file; edit only the root `.env`).
2. Backend:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn.exe main:app --reload --host 127.0.0.1 --port 8765
```

3. Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3001

For invoice photo storage, run `../purchase-entry/supabase/invoice_images.sql` (or the shared schema) in the Supabase SQL editor if needed.
