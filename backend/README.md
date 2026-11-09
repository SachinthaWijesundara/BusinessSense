# BusinessSense extract API

FastAPI service for InvoiceVisionAgent (Instructor + OpenAI, CLAHE preprocess).

Config lives in the **repo-root** `BusinessSense/.env` only (loaded by `main.py`).

```powershell
cd BusinessSense
copy .env.example .env
# fill OPENAI_API_KEY (and Supabase keys for the frontend)

cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn.exe main:app --reload --host 127.0.0.1 --port 8765
```

- `GET /health` — liveness
- `POST /extract` — multipart `file` image → invoice/receipt JSON, or `422` / `502` with `error_type`
